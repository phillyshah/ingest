"""Stage runner. Each stage is idempotent, writes its checkpoint, moves source_version.pipeline_state, and enqueues
the next stage under a fresh idempotency key (spec §5)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from moveai_db import J

from . import queue as q
from .config import PARSER_VERSION
from .extract import run_extraction
from .fetch import FetchError, fetch
from .llm import ExtractionModel, SchemaViolation, get_model
from .normalize import persist
from .parse import parse
from .rights import check, grant_for_source_version
from .source_policies import effective_domains, materialise_rights_grant, policy_for_url
from .storage import content_key, get_storage

NEXT = {
    "access_check": "fetch",
    "fetch": "parse",
    "parse": "extract",
    "extract": "normalize",
    "normalize": "validate",
    "validate": "enqueue_review",
    "enqueue_review": None,
}


class StageError(Exception):
    def __init__(self, error_class: str, message: str, *, permanent: bool | None = None, state: str | None = None):
        super().__init__(message)
        self.error_class, self.permanent, self.state = error_class, permanent, state


def _set_state(conn: psycopg.Connection, svid: Any, state: str, **cols: Any) -> None:
    sets = ", ".join(["pipeline_state=%s"] + [f"{k}=%s" for k in cols])
    conn.execute(f"update source_version set {sets} where id=%s", (state, *cols.values(), svid))


def _source(conn: psycopg.Connection, svid: Any) -> tuple[dict, dict]:
    sv = conn.execute("select * from source_version where id=%s", (svid,)).fetchone()
    src = conn.execute("select * from source where id=%s", (sv["source_id"],)).fetchone()
    return sv, src


def stage_access_check(conn: psycopg.Connection, job: dict) -> dict:
    sv, src = _source(conn, job["source_version_id"])
    if src["allowlist_state"] == "denied":
        _set_state(conn, sv["id"], "rights_hold")
        raise StageError("not_allowlisted", f"source {src['canonical_url']} is denied", permanent=True)
    if src["allowlist_state"] != "approved":
        # A per-source approval is one way in; a signed domain policy is the other, and it is the one that lets a
        # campaign reach a publisher nobody has hand-entered. Anything else is still refused here.
        pol = policy_for_url(conn, sv["final_url"] or src["canonical_url"])
        if not (pol and pol["effective"]):
            _set_state(conn, sv["id"], "rights_hold")
            raise StageError(
                "not_allowlisted",
                f"source {src['canonical_url']} allowlist_state={src['allowlist_state']}"
                + (
                    f"; the policy for {pol['domain']} is {pol['review_state']} and its terms are {pol['evidence_state']}"
                    if pol
                    else "; no publisher policy covers this domain"
                ),
                permanent=True,
            )
    grant = grant_for_source_version(conn, sv["id"])
    if grant is None:
        # A campaign creates source versions straight from a URL, with nobody to type permissions in. Derive them
        # from the domain policy instead. A policy that is unsigned yields a grant of `unknown`, which blocks —
        # the point is that the reviewer sees which publisher's terms are outstanding, not a blank record.
        materialise_rights_grant(conn, sv["id"], sv["final_url"] or src["canonical_url"])
        grant = grant_for_source_version(conn, sv["id"])
    for op in ("can_fetch", "can_process_with_model"):
        r = check(grant, op)
        if not r.allowed:
            _set_state(conn, sv["id"], "rights_hold")
            raise StageError(r.error_class or "rights_unknown", r.reason or op, permanent=True)
    _set_state(conn, sv["id"], "access_checked")
    return {
        "store_fulltext": check(grant, "can_store_fulltext").allowed,
        "store_excerpt": check(grant, "can_store_excerpt").allowed,
    }


def stage_fetch(conn: psycopg.Connection, job: dict) -> dict:
    sv, src = _source(conn, job["source_version_id"])
    url = job["payload"].get("url") or src["canonical_url"]
    try:
        f = fetch(url, effective_domains(conn))
    except FetchError as e:
        raise StageError(e.error_class, str(e), permanent=e.error_class in q.PERMANENT_ERRORS) from e
    prior = conn.execute(
        "select id from source_version where source_id=%s and content_sha256=%s and id<>%s and pipeline_state <> 'superseded' order by created_at desc limit 1",
        (sv["source_id"], f.sha256, sv["id"]),
    ).fetchone()
    if prior:
        # unchanged bytes: link and skip re-extraction (spec §5.3)
        _set_state(
            conn,
            sv["id"],
            "superseded",
            content_sha256=f.sha256,
            retrieved_at="now()",
            final_url=f.final_url,
            content_type=f.content_type,
            superseded_by_id=prior["id"],
            byte_size=len(f.content),
        )
        return {"unchanged": True, "prior_version_id": str(prior["id"]), "skip_next": True}
    grant = grant_for_source_version(conn, sv["id"])
    storage_ref = None
    if check(grant, "can_store_fulltext").allowed:
        storage_ref = get_storage().put(content_key(f.sha256, Path(urlparse(url).path).suffix or ".bin"), f.content, f.content_type)
    _set_state(
        conn,
        sv["id"],
        "fetched",
        content_sha256=f.sha256,
        retrieved_at="now()",
        final_url=f.final_url,
        content_type=f.content_type,
        etag=f.etag,
        last_modified=f.last_modified,
        storage_ref=storage_ref,
        byte_size=len(f.content),
    )
    return {
        "sha256": f.sha256,
        "content_type": f.content_type,
        "bytes": len(f.content),
        "stored": storage_ref is not None,
        "warnings": f.warnings,
        "url": url,
    }


def _load_bytes(conn: psycopg.Connection, sv: dict, src: dict, job: dict) -> tuple[bytes, str]:
    if sv["storage_ref"]:
        return get_storage().get(sv["storage_ref"]), sv["content_type"]
    # not stored: re-fetch transiently (permitted: can_fetch was checked) and never persist
    f = fetch(job["payload"].get("url") or src["canonical_url"], effective_domains(conn))
    return f.content, f.content_type


def stage_parse(conn: psycopg.Connection, job: dict) -> dict:
    sv, src = _source(conn, job["source_version_id"])
    content, ct = _load_bytes(conn, sv, src, job)
    try:
        doc = parse(content, ct, src["source_type"])
    except Exception as e:
        _set_state(conn, sv["id"], "parse_failed")
        raise StageError("parse_failed", str(e), permanent=True) from e
    _set_state(
        conn,
        sv["id"],
        "parsed",
        parser_version=PARSER_VERSION,
        parse_warnings=J(doc.warnings),
        declared_publication_date=doc.declared_publication_date,
        document_identity=doc.title or sv["document_identity"],
    )
    return {"blocks": len(doc.blocks), "warnings": doc.warnings, "kind": doc.kind}


def stage_extract(conn: psycopg.Connection, job: dict, model: ExtractionModel | None = None) -> dict:
    sv, src = _source(conn, job["source_version_id"])
    content, ct = _load_bytes(conn, sv, src, job)
    doc = parse(content, ct, src["source_type"])
    model = model or get_model()
    hint = None
    url = job["payload"].get("url") or src["canonical_url"]
    if url.startswith("file:"):
        p = urlparse(url).path
        hint = p if p.startswith("/") else str(Path.cwd() / (urlparse(url).netloc + p))
    try:
        out = run_extraction(doc, model, source_hint=hint)
    except SchemaViolation as e:
        _set_state(conn, sv["id"], "extraction_failed")
        raise StageError("schema_invalid", str(e), permanent=True) from e
    _set_state(conn, sv["id"], "extracted")
    return {
        "result": out.result.model_dump(mode="json"),
        "warnings": out.warnings,
        "review_flags": out.review_flags,
        "cost_usd": out.cost_usd,
    }


def stage_normalize(conn: psycopg.Connection, job: dict) -> dict:
    sv, src = _source(conn, job["source_version_id"])
    ext = conn.execute(
        "select checkpoint from ingestion_job where source_version_id=%s and stage='extract' and state='succeeded' order by finished_at desc limit 1",
        (sv["id"],),
    ).fetchone()
    if not ext:
        raise StageError("missing_upstream", "extract checkpoint not found")
    from .llm import ExtractionResult

    result = ExtractionResult.model_validate(ext["checkpoint"]["result"])
    flags = list(ext["checkpoint"].get("review_flags", []))
    grant = grant_for_source_version(conn, sv["id"])
    region = job["payload"].get("region", "unknown")
    if conn.execute(
        "select 1 from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s limit 1",
        (sv["id"],),
    ).fetchone():
        return {"already_normalized": True}
    created = persist(
        conn,
        source_version_id=sv["id"],
        source_row=src,
        result=result,
        review_flags=flags,
        region=region,
        excerpt_allowed=check(grant, "can_store_excerpt").allowed,
        tenant_id=job["tenant_id"],
    )
    _set_state(conn, sv["id"], "normalized")
    return {**created, "review_flags": flags}


def stage_validate(conn: psycopg.Connection, job: dict) -> dict:
    sv, src = _source(conn, job["source_version_id"])
    issues: list[str] = []
    rows = conn.execute(
        "select v.* from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id and d.upstream_table='source_version' where d.upstream_id=%s",
        (sv["id"],),
    ).fetchall()
    for v in rows:
        if not v["step_sequence"]:
            issues.append(f"{v['name']}: no instructions extracted")
        if not (v["source_reference"] or {}).get("locator"):
            issues.append(f"{v['name']}: missing source locator")
    conflicts = conn.execute(
        "select count(*) as n from evidence_claim where source_version_id=%s and conflicts_with_claim_id is not null",
        (sv["id"],),
    ).fetchone()["n"]
    _set_state(conn, sv["id"], "conflict_hold" if conflicts else "evidence_linked")
    return {"issues": issues, "conflicts": conflicts, "variants": len(rows)}


def stage_enqueue_review(conn: psycopg.Connection, job: dict) -> dict:
    sv, _ = _source(conn, job["source_version_id"])
    _set_state(conn, sv["id"], "pending_review")
    n = conn.execute(
        "select count(*) as n from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id and d.upstream_table='source_version' where d.upstream_id=%s and v.approval_state='pending_review'",
        (sv["id"],),
    ).fetchone()["n"]
    return {"queued_for_review": n}


STAGE_FN = {
    "access_check": stage_access_check,
    "fetch": stage_fetch,
    "parse": stage_parse,
    "extract": stage_extract,
    "normalize": stage_normalize,
    "validate": stage_validate,
    "enqueue_review": stage_enqueue_review,
}


def run_job(conn: psycopg.Connection, job: dict, *, model: ExtractionModel | None = None, commit: bool = False) -> str:
    """Execute one claimed job. Stage work runs inside a savepoint; failures roll back only the stage's writes.
    The caller owns the outer transaction (the worker commits; tests roll back)."""
    t0 = time.monotonic()
    fn = STAGE_FN[job["stage"]]
    try:
        try:
            with conn.transaction():
                result = fn(conn, job, model) if job["stage"] == "extract" else fn(conn, job)  # type: ignore[call-arg]
                q.checkpoint(conn, job["id"], result)
                q.succeed(
                    conn,
                    job["id"],
                    warnings=result.get("warnings", []) + result.get("review_flags", []),
                    cost_usd=float(result.get("cost_usd", 0)),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                nxt = NEXT[job["stage"]]
                if nxt and not result.get("skip_next"):
                    q.enqueue(
                        conn,
                        stage=nxt,
                        source_version_id=job["source_version_id"],
                        payload=job["payload"],
                        tenant_id=job["tenant_id"],
                        campaign_id=job["campaign_id"],
                        campaign_run_id=job["campaign_run_id"],
                        priority=job["priority"],
                    )
            state = "succeeded"
        except StageError as e:
            with conn.transaction():
                # the stage's own writes were rolled back with the savepoint; re-apply the hold state explicitly
                if e.error_class in ("not_allowlisted", "rights_unknown", "rights_denied"):
                    _set_state(conn, job["source_version_id"], "rights_hold")
                elif e.error_class == "parse_failed":
                    _set_state(conn, job["source_version_id"], "parse_failed")
                elif e.error_class == "schema_invalid":
                    _set_state(conn, job["source_version_id"], "extraction_failed")
                state = q.fail(conn, job["id"], e.error_class, str(e), permanent=e.permanent)
        except Exception as e:  # unexpected: retry with backoff
            with conn.transaction():
                state = q.fail(conn, job["id"], "unexpected", f"{type(e).__name__}: {e}")
        if job.get("campaign_id"):
            conn.execute(
                "insert into campaign_event(campaign_id, run_id, event, detail) values (%s,%s,'job',%s)",
                (
                    job["campaign_id"],
                    job["campaign_run_id"],
                    J({"job_id": str(job["id"]), "stage": job["stage"], "state": state}),
                ),
            )
            if job.get("campaign_run_id"):
                from .campaigns import reconcile_run

                reconcile_run(conn, job["campaign_run_id"])
    finally:
        if commit:
            conn.commit()
    return state


def run_all(
    conn: psycopg.Connection,
    *,
    worker_id: str = "inline",
    model: ExtractionModel | None = None,
    max_jobs: int = 500,
    commit: bool = False,
) -> list[tuple[str, str]]:
    """Drain the queue synchronously (tests, demo, `make seed`)."""
    done: list[tuple[str, str]] = []
    for _ in range(max_jobs):
        job = q.claim(conn, worker_id)
        if not job:
            break
        done.append((job["stage"], run_job(conn, job, model=model, commit=commit)))
    return done


def register_source_version(conn: psycopg.Connection, source_id: Any, *, url: str | None = None) -> Any:
    return conn.execute(
        "insert into source_version(source_id, final_url, pipeline_state) values (%s,%s,'discovered') returning id",
        (source_id, url),
    ).fetchone()["id"]
