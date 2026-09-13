"""Directed ingestion campaigns (spec §21): scope resolution, gap analysis, bounded runs, control actions,
lifecycle derived from real job/coverage state, and reconciled progress counters."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import psycopg
from moveai_contracts.matching import condition_names, name_matches
from moveai_db import J

from . import queue as q
from .condition_match import get_condition_matcher
from .fetch import canonicalize
from .source_policies import policy_for_url
from .terminology import resolve_code

HEARTBEAT_STALE_S = 60
EST_COST_PER_SOURCE_USD = 0.05  # reservation estimate; settled with actual model cost


class CampaignError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------ scope
def match_conditions(conn: psycopg.Connection, text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    out = []
    for c in conn.execute("select * from condition").fetchall():
        if any(name_matches(n, text) for n in condition_names(c)):
            out.append(c)
    return out


def suggested_conditions(conn: psycopg.Connection, text: str | None) -> list[dict[str, Any]]:
    """Fallback proposals for free text the exact match in `match_conditions` could not place.

    Never merged into the scope on its own — see condition_match.py's module docstring. Only called when the
    exact match already came up empty, so it never overrides or second-guesses a match that already succeeded.
    """
    if not text:
        return []
    candidates = conn.execute("select id, preferred_name, internal_code, synonyms from condition").fetchall()
    if not candidates:
        return []
    hits = get_condition_matcher().suggest(text, candidates)
    by_id = {str(c["id"]): c for c in candidates}
    out = []
    for h in hits:
        c = by_id.get(h["condition_id"])
        if not c:
            continue  # a matcher naming an id outside the supplied list is dropped, not trusted
        out.append(
            {
                "id": h["condition_id"],
                "code": c["internal_code"],
                "name": c["preferred_name"],
                "confidence": h["confidence"],
                "reason": h["reason"],
            }
        )
    return out


def conditions_for_codes(conn: psycopg.Connection, codes: list[str]) -> list[dict[str, Any]]:
    """Conditions a diagnosis code alone can select.

    Only `exact` mappings count. A `broader`/`narrower` mapping exists so a pack shows up when someone searches
    catalog by that code, but the code does not, by itself, establish that pack's population — a sprain ICD-10
    code says nothing about whether the sprain was later treated surgically, so mcl_repair maps S83.411A as
    `broader`, not `exact`. Treating every mapping as equally selecting would make the operative and nonoperative
    MCL pathways permanently "ambiguous" from the code alone, when the ambiguity is real and belongs to
    `procedure`/free text, not to this function silently picking one.
    """
    if not codes:
        return []
    return conn.execute(
        """select distinct c.* from condition c join diagnosis_mapping_version m on m.condition_id=c.id
             join terminology_code tc on tc.id=m.terminology_code_id
             where tc.code = any(%s) and m.relationship = 'exact'""",
        ([c.upper() for c in codes],),
    ).fetchall()


def scope_preview(conn: psycopg.Connection, tenant_id: Any, scope: dict[str, Any]) -> dict[str, Any]:
    svc_date = date.fromisoformat(scope["service_date"]) if scope.get("service_date") else date.today()
    resolved = [resolve_code(conn, c, svc_date) for c in scope.get("codes", [])]
    exact = match_conditions(conn, scope.get("ailment_text"))
    conds = {c["id"]: c for c in exact}
    for cid in scope.get("additional_condition_ids") or []:
        # Explicit operator acceptance of a proposed suggestion (or any other condition id) — the only path by
        # which a condition the exact text match did not find can enter scope. Never populated by this function.
        c = conn.execute("select * from condition where id=%s", (cid,)).fetchone()
        if c:
            conds[c["id"]] = c
    for c in conditions_for_codes(conn, [r["code"] for r in resolved if r["resolved"]]):
        conds[c["id"]] = c
    unresolved_text = []
    if scope.get("ailment_text") and not exact:
        unresolved_text.append(scope["ailment_text"])
    # Only offered when the exact match found nothing at all — a fallback for the case it exists to help, not a
    # second opinion on a match that already succeeded.
    suggested = suggested_conditions(conn, scope.get("ailment_text")) if scope.get("ailment_text") and not exact else []
    blocking: list[str] = []
    if not conds:
        blocking.append("no condition could be interpreted from the ailment text or codes")
    if any(not r["resolved"] for r in resolved):
        blocking.append("one or more codes did not resolve against the effective terminology release")
    if len(conds) > 1:
        blocking.append(f"ambiguous scope: {len(conds)} conditions matched; narrow the request before activation")
    coverage: dict[str, Any] = {}
    missing: list[str] = []
    for c in conds.values():
        protos = conn.execute(
            "select id, name, approval_state, phases from protocol_version where condition_id=%s and approval_state not in ('rejected','withdrawn','superseded','invalidated')",
            (c["id"],),
        ).fetchall()
        uses = conn.execute(
            "select approval_state, count(*) as n from clinical_use_version where condition_id=%s group by approval_state",
            (c["id"],),
        ).fetchall()
        approved_protos = [p for p in protos if p["approval_state"] in ("approved", "published")]
        coverage[c["internal_code"]] = {
            "protocols": len(protos),
            "approved_protocols": len(approved_protos),
            "clinical_uses": {u["approval_state"]: u["n"] for u in uses},
            "phases_defined": sorted({ph["name"] for p in protos for ph in p["phases"]}),
        }
        if not approved_protos:
            missing.append(f"{c['preferred_name']}: no approved pathway; placeholder structure only")
        for p in protos:
            for ph in p["phases"]:
                if ph.get("gaps"):
                    missing.append(f"{c['preferred_name']} / {ph['name']}: {', '.join(ph['gaps'])}")
    sources = conn.execute(
        "select canonical_url, publisher, allowlist_state from source where allowlist_state='approved' order by created_at"
    ).fetchall()
    strategy = [f"supplied source: {u}" for u in scope.get("supplied_source_urls", [])]
    policies = conn.execute("select * from source_policy order by publisher").fetchall()
    readable = [p for p in policies if p["effective"]]
    if scope.get("source_policy") != "supplied_only":
        strategy += [f"allowlisted source: {s['canonical_url']}" for s in sources[:20]]
        # Honest about what a signed publisher means today: its pages *may be read* when a URL on it is supplied
        # or already linked to the condition. Nothing searches the publisher for relevant pages — the earlier
        # wording ("allowlisted publisher: …") read as if the campaign would go and look there, and it will not.
        strategy += [
            f"may read from {p['publisher']} ({p['domain']}, {p['license_id']}) when a URL on it is supplied — no automatic discovery yet"
            for p in readable
        ]
    strategy.append(
        "no automatic discovery: only supplied URLs and sources already linked to the condition are read; "
        "domains outside the allowlist are recorded as pending, never fetched"
    )
    # Only a campaign with nothing at all to read is blocked. Supplied URLs and per-source approvals are both
    # legitimate ways in, and the fixture-backed runs use them.
    if not readable and not scope.get("supplied_source_urls") and not sources:
        awaiting = sum(1 for p in policies if p["review_state"] == "pending")
        blocking.append(
            "nothing to read: no source is approved, no URL was supplied, and "
            + (
                f"{awaiting} publisher policies are waiting for a rights reviewer to accept their licence terms"
                if awaiting
                else "the curated allowlist is empty"
            )
        )
    return {
        "interpreted_conditions": [
            {
                "id": str(c["id"]),
                "code": c["internal_code"],
                "name": c["preferred_name"],
                "ambiguity_flags": c["ambiguity_flags"],
            }
            for c in conds.values()
        ],
        "resolved_codes": resolved,
        "unresolved_text": unresolved_text,
        "suggested_conditions": [s for s in suggested if s["id"] not in {str(cid) for cid in conds}],
        "included": {
            "conditions": [c["preferred_name"] for c in conds.values()],
            "refinements": scope.get("refinements", {}),
        },
        "excluded": scope.get("exclusion", {}),
        "existing_coverage": coverage,
        "missing": sorted(set(missing)),
        "proposed_strategy": strategy,
        "max_work": scope["limits"],
        "clinical_review_requirements": [
            "every extracted variant and clinical use requires PT review",
            "protocol publication requires clinical lead approval",
            "rights reviewer clears any graphic before patient display",
        ],
        "can_start": not blocking and bool(scope.get("scope_confirmed")),
        "blocking_reasons": blocking + ([] if scope.get("scope_confirmed") else ["scope not confirmed"]),
    }


# ------------------------------------------------------------ create / revise
def create(conn: psycopg.Connection, tenant_id: Any, user_id: Any, body: dict[str, Any]) -> dict[str, Any]:
    scope = body["scope"]
    camp = conn.execute(
        "insert into ingestion_campaign(tenant_id, owner_id, reviewer_id, title, priority) values (%s,%s,%s,%s,%s) returning *",
        (
            tenant_id,
            body.get("owner_id") or user_id,
            body.get("reviewer_id"),
            body["title"],
            scope.get("priority", 100),
        ),
    ).fetchone()
    sv = _scope_version(conn, camp["id"], 1, scope, user_id)
    conn.execute("update ingestion_campaign set current_scope_version_id=%s where id=%s", (sv["id"], camp["id"]))
    _event(conn, camp["id"], None, "created", user_id, {"title": body["title"]})
    return get(conn, tenant_id, camp["id"])


def _scope_version(conn: psycopg.Connection, campaign_id: Any, version: int, scope: dict[str, Any], user_id: Any) -> dict:
    preview = scope_preview(conn, None, scope)
    return conn.execute(
        """insert into campaign_scope_version(campaign_id, version, ailment_text, codes, condition_ids, inclusion, exclusion, refinements, supplied_source_urls,
             desired_output, source_policy, limits, acceptance_criteria, media_policy, authorized_by, authorized_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
        (
            campaign_id,
            version,
            scope.get("ailment_text"),
            J(preview["resolved_codes"]),
            [uuid.UUID(c["id"]) for c in preview["interpreted_conditions"]],
            J(preview["included"]),
            J(scope.get("exclusion", {})),
            J(scope.get("refinements", {})),
            scope.get("supplied_source_urls", []),
            scope.get("desired_output", "text_first_phased_plan_templates"),
            scope.get("source_policy", "allowlist_only"),
            J(scope["limits"]),
            J(scope.get("acceptance_criteria", [])),
            scope.get("media_policy", "reference_only"),
            user_id if scope.get("scope_confirmed") else None,
            "now()" if scope.get("scope_confirmed") else None,
        ),
    ).fetchone()


def confirm_scope(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any) -> dict[str, Any]:
    """Authorize the campaign's current scope version so a run may start (spec §21B).

    A separate operation from revising, because it is a separate act: the operator is saying "your reading of what
    I asked for is correct", not changing what they asked for. Routing it through revise_scope would mean the UI
    had to reconstruct and resend the entire scope to tick one box, and any field it failed to round-trip — the
    resolved codes are stored as objects, not the strings the revision expects — would be silently reset.

    Authorization is recorded on the version it applies to, so a later revision arrives unconfirmed and has to be
    confirmed again. That is the point: the person signed off on one interpretation, not on the campaign forever.
    """
    camp = _camp(conn, tenant_id, campaign_id)
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    if scope["authorized_by"]:
        return get(conn, tenant_id, campaign_id)  # idempotent: confirming twice is not an error

    preview = preview_for_scope_version(conn, tenant_id, scope)
    # `can_start` includes "scope not confirmed", which is exactly what this call resolves; the other reasons are
    # real and must still block. Confirming an ambiguous or uninterpretable scope would authorize spending against
    # a request the system does not understand.
    remaining = [r for r in preview["blocking_reasons"] if r != "scope not confirmed"]
    if remaining:
        raise CampaignError("scope_blocked", "; ".join(remaining), 422)

    conn.execute(
        "update campaign_scope_version set authorized_by=%s, authorized_at=now() where id=%s",
        (user_id, scope["id"]),
    )
    _event(conn, camp["id"], None, "scope_confirmed", user_id, {"version": scope["version"]})
    return get(conn, tenant_id, campaign_id)


def preview_for_scope_version(conn: psycopg.Connection, tenant_id: Any, scope: dict[str, Any]) -> dict[str, Any]:
    """Re-run the scope preview for a stored scope version.

    Stored codes are resolution results (objects), while scope_preview takes the codes as the operator typed them,
    so they are mapped back. Both `start` and `confirm_scope` need this, and they must agree — a campaign that
    confirms and then refuses to start would be worse than one that never confirmed.
    """
    return scope_preview(
        conn,
        tenant_id,
        {
            "ailment_text": scope["ailment_text"],
            "codes": [c["code"] for c in scope["codes"]],
            "limits": scope["limits"],
            "supplied_source_urls": scope["supplied_source_urls"],
            "source_policy": scope["source_policy"],
            "scope_confirmed": bool(scope["authorized_by"]),
            "exclusion": scope["exclusion"],
            "refinements": scope["refinements"],
            # The stored version's condition_ids is itself the earlier preview's resolved set — exact matches,
            # code-derived matches, and any suggestion the operator had already accepted. Carrying it forward here
            # is what makes an accepted suggestion survive a later re-preview (confirm_scope, start) rather than
            # only ever existing for the one preview call it was accepted on.
            "additional_condition_ids": [str(cid) for cid in scope["condition_ids"]],
        },
    )


def revise_scope(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any, scope: dict[str, Any]) -> dict[str, Any]:
    camp = _camp(conn, tenant_id, campaign_id)
    n = conn.execute("select coalesce(max(version),0) as v from campaign_scope_version where campaign_id=%s", (camp["id"],)).fetchone()["v"]
    sv = _scope_version(conn, camp["id"], n + 1, scope, user_id)
    conn.execute(
        "update ingestion_campaign set current_scope_version_id=%s, priority=%s where id=%s",
        (sv["id"], scope.get("priority", camp["priority"]), camp["id"]),
    )
    _event(
        conn,
        camp["id"],
        None,
        "scope_revised",
        user_id,
        {"version": n + 1, "active_run_affected": bool(_active_run(conn, camp["id"]))},
    )
    return get(conn, tenant_id, camp["id"])


# ------------------------------------------------------------ runs & controls
def _camp(conn: psycopg.Connection, tenant_id: Any, campaign_id: Any) -> dict:
    row = conn.execute("select * from ingestion_campaign where id=%s and tenant_id=%s", (campaign_id, tenant_id)).fetchone()
    if not row:
        raise CampaignError("not_found", "campaign not found", 404)
    return row


def _active_run(conn: psycopg.Connection, campaign_id: Any) -> dict | None:
    return conn.execute(
        "select * from campaign_run where campaign_id=%s and state in ('pending','running','paused') order by run_number desc limit 1",
        (campaign_id,),
    ).fetchone()


def _event(
    conn: psycopg.Connection,
    campaign_id: Any,
    run_id: Any,
    event: str,
    actor: Any,
    detail: dict | None = None,
) -> None:
    conn.execute(
        "insert into campaign_event(campaign_id, run_id, event, actor_id, detail) values (%s,%s,%s,%s,%s)",
        (campaign_id, run_id, event, actor, J(detail or {})),
    )


def start(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any) -> dict[str, Any]:
    camp = _camp(conn, tenant_id, campaign_id)
    if camp["control"] == "cancelled":
        raise CampaignError("cancelled", "cancelled campaigns cannot start; propose a new run from it instead")
    if _active_run(conn, camp["id"]):
        raise CampaignError("already_running", "a run is already active")
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    if not scope["authorized_by"]:
        raise CampaignError(
            "scope_not_confirmed",
            "check the scope preview on this campaign and confirm the interpretation is right before starting",
            422,
        )
    preview = preview_for_scope_version(conn, tenant_id, scope)
    if not preview["can_start"]:
        raise CampaignError("scope_blocked", "; ".join(preview["blocking_reasons"]), 422)
    n = conn.execute("select coalesce(max(run_number),0) as n from campaign_run where campaign_id=%s", (camp["id"],)).fetchone()["n"]
    run = conn.execute(
        "insert into campaign_run(campaign_id, scope_version_id, run_number, state, started_at, heartbeat_at) values (%s,%s,%s,'running',now(),now()) returning *",
        (camp["id"], scope["id"], n + 1),
    ).fetchone()
    conn.execute("update ingestion_campaign set control='active' where id=%s", (camp["id"],))
    dispatched = _dispatch_sources(conn, tenant_id, camp, scope, run)
    conn.execute(
        "update campaign_run set discovery_closed_at=now(), stop_reason=%s where id=%s",
        (
            "discovery closed: bounded source list dispatched"
            if dispatched["dispatched"]
            else "no eligible sources within scope and limits",
            run["id"],
        ),
    )
    _event(conn, camp["id"], run["id"], "started", user_id, dispatched)
    return get(conn, tenant_id, camp["id"])


def _pending_reason(pol: dict | None) -> str:
    """Why a URL was parked rather than fetched, in words the operator can act on.

    'pending allowlist approval' told nobody what to do next. Naming the publisher and the exact missing step is
    the difference between a Needs Attention card that gets resolved and one that gets ignored.
    """
    if pol is None:
        return "no publisher policy covers this domain; add it to the curated allowlist before it can be read"
    if pol["review_state"] == "rejected":
        return f"{pol['publisher']} was reviewed and rejected: {pol['review_note'] or 'no reason recorded'}"
    if pol["evidence_state"] == "uncaptured":
        return f"{pol['publisher']}: the licence terms have not been read yet"
    if pol["evidence_state"] == "unreachable":
        return f"{pol['publisher']}: the licence terms page could not be reached on the last attempt"
    if pol["evidence_state"] == "drifted":
        return f"{pol['publisher']} changed its terms since they were signed; they need re-reading"
    if pol["review_state"] != "signed":
        return f"{pol['publisher']}: the licence terms are captured and waiting for a rights reviewer to accept them"
    if pol["can_fetch"] != "allowed":
        return f"{pol['publisher']}'s licence ({pol['license_id']}) does not permit reading: can_fetch is {pol['can_fetch']}"
    return f"{pol['publisher']}: the signature no longer covers the terms on record"


def _dispatch_sources(conn: psycopg.Connection, tenant_id: Any, camp: dict, scope: dict, run: dict) -> dict[str, Any]:
    """Bounded, request-driven discovery: supplied URLs + allowlisted sources already linked to the scoped conditions.
    Unknown domains become pending items (Needs Attention), never fetches."""
    limits = scope["limits"]
    # The body region every extracted exercise from this run is filed under. It was hard-coded "unknown", which
    # left campaign-sourced exercises unfindable by region in the catalog. The scoped conditions know their region;
    # when they agree, use it. When they do not (or there is none), "unknown" stays honest — never a guess.
    regions = {
        r["body_region"]
        for r in conn.execute("select distinct body_region from condition where id = any(%s)", (list(scope["condition_ids"]),)).fetchall()
    }
    region = regions.pop() if len(regions) == 1 else "unknown"
    candidates: list[dict[str, Any]] = []
    for url in scope["supplied_source_urls"]:
        try:
            cu = canonicalize(url)
        except Exception as e:  # noqa: BLE001
            candidates.append({"url": url, "problem": str(e)})
            continue
        src = conn.execute("select * from source where canonical_url=%s", (cu,)).fetchone()
        if not src:
            src = conn.execute(
                "insert into source(tenant_id, canonical_url, source_type, allowlist_state) values (%s,%s,%s,'pending') returning *",
                (tenant_id, cu, "pdf" if cu.lower().endswith(".pdf") else "html"),
            ).fetchone()
        candidates.append({"url": cu, "source": src})
    if scope["source_policy"] != "supplied_only":
        for cid in scope["condition_ids"]:
            rows = conn.execute(
                """select distinct s.* from source s join source_version sv on sv.source_id=s.id join evidence_claim ec on ec.source_version_id=sv.id
                     join clinical_use_version cu on ec.id = any(cu.supporting_claim_ids) where cu.condition_id=%s and s.allowlist_state='approved'""",
                (cid,),
            ).fetchall()
            candidates += [{"url": r["canonical_url"], "source": r} for r in rows]
    seen: set[str] = set()
    dispatched, pending, skipped = [], [], []
    for c in candidates:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        if "problem" in c:
            skipped.append({"url": c["url"], "reason": c["problem"]})
            continue
        src = c["source"]
        pol = policy_for_url(conn, c["url"])
        if src["allowlist_state"] != "approved" and not (pol and pol["effective"]):
            pending.append(c["url"])
            _item(
                conn,
                camp["id"],
                run["id"],
                "source",
                src["id"],
                "pending",
                {"reason": _pending_reason(pol), "domain": urlparse(c["url"]).hostname},
            )
            continue
        if len(dispatched) >= int(limits["max_sources"]):
            skipped.append({"url": c["url"], "reason": "max_sources reached"})
            continue
        if not conn.execute(
            "select reserve_budget(%s,null,%s::numeric,1,0::bigint) as ok",
            (run["id"], EST_COST_PER_SOURCE_USD),
        ).fetchone()["ok"]:
            skipped.append({"url": c["url"], "reason": "budget/search-request limit reached"})
            continue
        svid = conn.execute(
            "insert into source_version(source_id, final_url, pipeline_state) values (%s,%s,'discovered') returning id",
            (src["id"], c["url"]),
        ).fetchone()["id"]
        grant = conn.execute(
            "select * from rights_grant where source_version_id in (select id from source_version where source_id=%s) order by created_at desc limit 1",
            (src["id"],),
        ).fetchone()
        if grant:  # carry the source's latest rights grant forward to the new version
            cols = [
                k
                for k in grant
                if k.startswith("can_")
                or k
                in (
                    "attribution_required",
                    "attribution_text",
                    "territory",
                    "expires_at",
                    "permission_evidence",
                    "rights_reviewer_id",
                )
            ]
            conn.execute(
                f"insert into rights_grant(source_version_id, {','.join(cols)}) values (%s,{','.join(['%s'] * len(cols))})",
                (svid, *[J(grant[k]) if k == "permission_evidence" else grant[k] for k in cols]),
            )
        job = q.enqueue(
            conn,
            stage="access_check",
            source_version_id=svid,
            payload={"url": c["url"], "region": region},
            tenant_id=tenant_id,
            campaign_id=camp["id"],
            campaign_run_id=run["id"],
            priority=camp["priority"],
        )
        _item(
            conn,
            camp["id"],
            run["id"],
            "source_version",
            svid,
            "new",
            {"url": c["url"], "job_id": str(job["id"])},
        )
        dispatched.append(c["url"])
    return {"dispatched": dispatched, "pending_allowlist": pending, "skipped": skipped}


def _item(
    conn: psycopg.Connection,
    campaign_id: Any,
    run_id: Any,
    table: str,
    item_id: Any,
    disposition: str,
    detail: dict,
) -> None:
    conn.execute(
        """insert into campaign_item(campaign_id, run_id, item_table, item_id, disposition, detail) values (%s,%s,%s,%s,%s,%s)
                    on conflict (campaign_id, item_table, item_id) do update set disposition=excluded.disposition, detail=excluded.detail""",
        (campaign_id, run_id, table, item_id, disposition, J(detail)),
    )


def _run_action(conn: psycopg.Connection, tenant_id: Any, campaign_id: Any, expected_revision: int) -> tuple[dict, dict]:
    camp = _camp(conn, tenant_id, campaign_id)
    run = _active_run(conn, camp["id"])
    if not run:
        raise CampaignError("no_active_run", "no active run", 409)
    run = conn.execute("select * from campaign_run where id=%s for update", (run["id"],)).fetchone()
    if run["revision"] != expected_revision:
        raise CampaignError("stale_revision", f"expected revision {expected_revision}, current {run['revision']}")
    return camp, run


def pause(conn, tenant_id, user_id, campaign_id, expected_revision):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    if run["state"] != "running":
        raise CampaignError("not_running", f"run is {run['state']}")
    conn.execute(
        "update campaign_run set state='paused', paused_at=now(), revision=revision+1 where id=%s",
        (run["id"],),
    )
    conn.execute("update ingestion_campaign set control='paused' where id=%s", (camp["id"],))
    inflight = conn.execute(
        "select count(*) as n from ingestion_job where campaign_run_id=%s and state='running'", (run["id"],)
    ).fetchone()["n"]
    _event(conn, camp["id"], run["id"], "paused", user_id, {"in_flight_jobs_finishing": inflight})
    return get(conn, tenant_id, camp["id"])


def resume(conn, tenant_id, user_id, campaign_id, expected_revision):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    if run["state"] != "paused":
        raise CampaignError("not_paused", f"run is {run['state']}")
    conn.execute(
        "update campaign_run set state='running', paused_at=null, heartbeat_at=now(), revision=revision+1 where id=%s",
        (run["id"],),
    )
    conn.execute("update ingestion_campaign set control='active' where id=%s", (camp["id"],))
    _event(conn, camp["id"], run["id"], "resumed", user_id, {})
    return get(conn, tenant_id, camp["id"])


def cancel(conn, tenant_id, user_id, campaign_id, expected_revision, reason: str | None = None):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    conn.execute(
        "update campaign_run set state='cancelled', finished_at=now(), stop_reason=%s, revision=revision+1 where id=%s",
        (f"cancelled by administrator: {reason or 'no reason given'}", run["id"]),
    )
    n = conn.execute(
        "update ingestion_job set state='cancelled', finished_at=now() where campaign_run_id=%s and state='queued' returning id",
        (run["id"],),
    ).fetchall()
    conn.execute(
        "update ingestion_campaign set control='cancelled', closure_reason=%s where id=%s",
        (reason, camp["id"]),
    )
    _event(
        conn,
        camp["id"],
        run["id"],
        "cancelled",
        user_id,
        {"queued_jobs_cancelled": len(n), "results_retained": True},
    )
    return get(conn, tenant_id, camp["id"])


def retry_failed(conn, tenant_id, user_id, campaign_id, expected_revision):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    rows = conn.execute(
        "select id, error_class from ingestion_job where campaign_run_id=%s and state in ('dead_letter','failed')",
        (run["id"],),
    ).fetchall()
    retried = [r["id"] for r in rows if r["error_class"] not in q.PERMANENT_ERRORS and q.retry_dead_letter(conn, r["id"])]
    conn.execute("update campaign_run set revision=revision+1, state='running' where id=%s", (run["id"],))
    _event(
        conn,
        camp["id"],
        run["id"],
        "retry_failed",
        user_id,
        {"retried": len(retried), "not_retryable": len(rows) - len(retried)},
    )
    return get(conn, tenant_id, camp["id"])


# ------------------------------------------------------------ reconciliation, lifecycle, cards
def reconcile_run(conn: psycopg.Connection, run_id: Any) -> dict:
    run = conn.execute("select * from campaign_run where id=%s", (run_id,)).fetchone()
    jobs = {
        r["state"]: r["n"]
        for r in conn.execute(
            "select state, count(*) as n from ingestion_job where campaign_run_id=%s group by state",
            (run_id,),
        ).fetchall()
    }
    active = jobs.get("queued", 0) + jobs.get("running", 0)
    last_hb = conn.execute("select max(heartbeat_at) as hb from ingestion_job where campaign_run_id=%s", (run_id,)).fetchone()["hb"]
    if last_hb:
        conn.execute(
            "update campaign_run set heartbeat_at=greatest(coalesce(heartbeat_at, %s), %s) where id=%s",
            (last_hb, last_hb, run_id),
        )
    if run["state"] == "running" and active == 0 and run["discovery_closed_at"]:
        conn.execute(
            "update campaign_run set state='finished', finished_at=now(), stop_reason=coalesce(stop_reason,'') || '; queue drained' where id=%s",
            (run_id,),
        )
    spent = conn.execute("select coalesce(sum(cost_usd),0) as c from ingestion_job where campaign_run_id=%s", (run_id,)).fetchone()["c"]
    progress = {"jobs": jobs, "spent_usd_actual": float(spent)}
    conn.execute("update campaign_run set progress=%s where id=%s", (J(progress), run_id))
    return conn.execute("select * from campaign_run where id=%s", (run_id,)).fetchone()


def counts(conn: psycopg.Connection, campaign_id: Any) -> dict[str, int]:
    c: dict[str, int] = {}
    for r in conn.execute(
        "select disposition, count(*) as n from campaign_item where campaign_id=%s and item_table in ('source_version','source') group by disposition",
        (campaign_id,),
    ).fetchall():
        c[f"sources_{r['disposition']}"] = r["n"]
    c["sources_discovered"] = sum(v for k, v in c.items() if k.startswith("sources_"))
    c["sources_processed"] = conn.execute(
        "select count(distinct source_version_id) as n from ingestion_job where campaign_id=%s and stage='enqueue_review' and state='succeeded'",
        (campaign_id,),
    ).fetchone()["n"]
    for r in conn.execute("select state, count(*) as n from ingestion_job where campaign_id=%s group by state", (campaign_id,)).fetchall():
        c[f"jobs_{r['state']}"] = r["n"]
    v = conn.execute(
        """select count(*) filter (where v.duplicate_of_entity_id is null) as new_variants, count(*) filter (where v.duplicate_of_entity_id is not null) as duplicates,
                  count(*) filter (where v.approval_state='pending_review') as awaiting_review, count(*) filter (where v.approval_state='approved') as approved,
                  count(*) filter (where v.approval_state='published') as published, count(*) filter (where v.approval_state='rejected') as rejected
             from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id and d.upstream_table='source_version'
             join campaign_item ci on ci.item_id = d.upstream_id and ci.item_table='source_version' where ci.campaign_id=%s""",
        (campaign_id,),
    ).fetchone()
    c.update({k: int(v[k] or 0) for k in v})
    c["evidence_linked"] = conn.execute(
        "select count(*) as n from evidence_claim ec join campaign_item ci on ci.item_id=ec.source_version_id and ci.item_table='source_version' where ci.campaign_id=%s",
        (campaign_id,),
    ).fetchone()["n"]
    c["reused_variants"] = conn.execute(
        "select count(*) as n from campaign_item where campaign_id=%s and item_table='exercise_variant_version' and disposition='reused'",
        (campaign_id,),
    ).fetchone()["n"]
    return c


def derive_lifecycle(conn: psycopg.Connection, camp: dict, run: dict | None, cnt: dict[str, int]) -> tuple[str, list[str], str | None]:
    blockers: list[str] = []
    if camp["control"] == "cancelled":
        return (
            ("closed_incomplete" if camp["lifecycle"] != "complete" else "complete"),
            ["cancelled"],
            "propose a new run if the request is still needed",
        )
    if not run:
        # Say which of the two it is. "confirm scope and start" was shown whether or not the scope was already
        # confirmed, so a draft waiting on confirmation looked identical to one ready to go — and pressing Start
        # was the only way to discover the difference.
        scope = conn.execute("select authorized_by from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
        if not (scope and scope["authorized_by"]):
            return "draft", ["the scope has not been confirmed"], "check the interpretation and confirm the scope"
        return "draft", [], "start the run"
    if run["state"] == "pending":
        return "queued", [], None
    if run["state"] in ("running", "paused"):
        if cnt.get("jobs_dead_letter", 0):
            blockers.append(f"{cnt['jobs_dead_letter']} job(s) dead-lettered; retry or decide")
        if cnt.get("sources_pending", 0):
            blockers.append(f"{cnt['sources_pending']} source(s) pending allowlist approval")
        active = cnt.get("jobs_queued", 0) + cnt.get("jobs_running", 0)
        if active == 0 and blockers:
            return "needs_attention", blockers, blockers[0]
        return "running", blockers, None
    # finished / cancelled run
    if cnt.get("jobs_dead_letter", 0):
        blockers.append(f"{cnt['jobs_dead_letter']} job(s) dead-lettered")
    if cnt.get("jobs_failed", 0):
        blockers.append(f"{cnt['jobs_failed']} job(s) failed permanently (rights/allowlist/schema)")
    if cnt.get("sources_pending", 0):
        blockers.append(f"{cnt['sources_pending']} source(s) pending allowlist approval")
    checks = conn.execute(
        "select state, count(*) as n from campaign_coverage_check where campaign_id=%s group by state",
        (camp["id"],),
    ).fetchall()
    ck = {r["state"]: r["n"] for r in checks}
    if ck and ck.get("met", 0) == sum(ck.values()) and cnt.get("published", 0) > 0 and cnt.get("awaiting_review", 0) == 0:
        return "complete", [], None
    if cnt.get("awaiting_review", 0) or cnt.get("approved", 0) or cnt.get("published", 0):
        return "pt_review", blockers, "PT reviews candidates; clinical lead publishes"
    blockers.append("the run had nothing to read: no document URLs were given and no accepted source is linked to this condition yet")
    return (
        "needs_attention",
        blockers,
        "add document URLs from a publisher accepted on the Sources page, or upload a PDF there instead",
    )


def card(conn: psycopg.Connection, camp: dict) -> dict[str, Any]:
    run = (
        _active_run(conn, camp["id"])
        or conn.execute("select * from campaign_run where campaign_id=%s order by run_number desc limit 1", (camp["id"],)).fetchone()
    )
    if run:
        run = reconcile_run(conn, run["id"])
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    cnt = counts(conn, camp["id"])
    lifecycle, blockers, next_action = derive_lifecycle(conn, camp, run, cnt)
    if lifecycle != camp["lifecycle"]:
        conn.execute("update ingestion_campaign set lifecycle=%s where id=%s", (lifecycle, camp["id"]))
    owner = conn.execute("select display_name from app_user where id=%s", (camp["owner_id"],)).fetchone() if camp["owner_id"] else None
    reviewer = (
        conn.execute("select display_name from app_user where id=%s", (camp["reviewer_id"],)).fetchone() if camp["reviewer_id"] else None
    )
    now = datetime.now(UTC)
    hb = run["heartbeat_at"] if run else None
    stale = bool(
        run
        and run["state"] == "running"
        and (hb is None or now - hb > timedelta(seconds=HEARTBEAT_STALE_S))
        and (cnt.get("jobs_queued", 0) + cnt.get("jobs_running", 0)) > 0
    )
    budget = run["budget"] if run else {"reserved_usd": 0, "spent_usd": 0}
    spend = float(budget.get("spent_usd", 0)) + float((run["progress"] or {}).get("spent_usd_actual", 0) if run else 0)
    conds = conn.execute("select preferred_name from condition where id = any(%s)", (scope["condition_ids"],)).fetchall() if scope else []
    allowed = allowed_actions(camp, run, scope)
    cov = {
        r["state"]: r["n"]
        for r in conn.execute(
            "select state, count(*) as n from campaign_coverage_check where campaign_id=%s group by state",
            (camp["id"],),
        ).fetchall()
    }
    activity = None
    if run and run["state"] == "running":
        activity = f"{cnt.get('jobs_running', 0)} running, {cnt.get('jobs_queued', 0)} queued"
    elif run:
        activity = f"run {run['run_number']} {run['state']}: {run['stop_reason'] or ''}".strip()
    return {
        "id": str(camp["id"]),
        "title": camp["title"],
        "lifecycle": lifecycle,
        "control": camp["control"],
        "priority": camp["priority"],
        "owner": owner["display_name"] if owner else None,
        "reviewer": reviewer["display_name"] if reviewer else None,
        "condition_summary": ", ".join(c["preferred_name"] for c in conds) or (scope["ailment_text"] if scope else None),
        "scope_summary": (f"{scope['desired_output']}; {scope['source_policy']}; media {scope['media_policy']}" if scope else None),
        "current_activity": activity,
        "last_update": camp["updated_at"],
        "heartbeat_at": hb,
        "heartbeat_stale": stale,
        "counts": cnt,
        "spend_usd": round(spend, 4),
        "cap_usd": float(scope["limits"]["max_usd"]) if scope else 0.0,
        "active_seconds": int(((run["finished_at"] or now) - run["started_at"]).total_seconds()) if run and run["started_at"] else 0,
        "coverage": cov,
        "blockers": blockers,
        "next_human_action": next_action,
        "allowed_actions": allowed,
        "run_id": str(run["id"]) if run else None,
        "run_revision": run["revision"] if run else None,
    }


def allowed_actions(camp: dict, run: dict | None, scope: dict | None) -> list[str]:
    a = ["edit", "preview_scope", "assign_reviewer", "change_priority", "propose_new_run"]
    if camp["control"] == "cancelled":
        return a
    if not run or run["state"] in ("finished", "cancelled"):
        # Offered alongside start, not instead of it: pressing start on an unconfirmed scope still gives a clear
        # refusal, and hiding the button people are looking for is its own kind of dead end.
        if scope is not None and not scope["authorized_by"]:
            a.append("confirm_scope")
        a.append("start")
    elif run["state"] == "running":
        a += ["pause", "cancel", "retry_failed"]
    elif run["state"] == "paused":
        a += ["resume", "cancel", "retry_failed"]
    elif run["state"] == "pending":
        a += ["cancel"]
    return a


def get(conn: psycopg.Connection, tenant_id: Any, campaign_id: Any) -> dict[str, Any]:
    camp = _camp(conn, tenant_id, campaign_id)
    c = card(conn, camp)
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    runs = conn.execute(
        "select id, run_number, state, revision, started_at, finished_at, stop_reason, budget, progress, heartbeat_at from campaign_run where campaign_id=%s order by run_number",
        (camp["id"],),
    ).fetchall()
    checks = conn.execute("select * from campaign_coverage_check where campaign_id=%s order by criterion", (camp["id"],)).fetchall()
    warnings = []
    if scope and not scope["authorized_by"]:
        warnings.append("scope not confirmed")
    return {
        **c,
        "scope": {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in scope.items()} if scope else {},
        "runs": [{**r, "id": str(r["id"])} for r in runs],
        "coverage_checks": [{**k, "id": str(k["id"])} for k in checks],
        "limits": scope["limits"] if scope else {},
        "budget": runs[-1]["budget"] if runs else {},
        "warnings": warnings,
        "maintenance_enabled": camp["maintenance_enabled"],
    }


def list_cards(
    conn: psycopg.Connection,
    tenant_id: Any,
    *,
    lifecycle: str | None = None,
    control: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select * from ingestion_campaign where tenant_id=%s and deleted_at is null order by priority, created_at",
        (tenant_id,),
    ).fetchall()
    out = []
    for r in rows:
        c = card(conn, r)
        if not include_archived and c["control"] == "cancelled" and not lifecycle:
            continue
        if lifecycle and c["lifecycle"] != lifecycle:
            continue
        if control and c["control"] != control:
            continue
        out.append(c)
    return out


def set_coverage(
    conn: psycopg.Connection,
    tenant_id: Any,
    user_id: Any,
    campaign_id: Any,
    criterion: str,
    state: str,
    evidence: dict | None,
) -> dict:
    camp = _camp(conn, tenant_id, campaign_id)
    run = _active_run(conn, camp["id"])
    return conn.execute(
        """insert into campaign_coverage_check(campaign_id, run_id, criterion, state, evidence, reviewer_id) values (%s,%s,%s,%s,%s,%s)
           on conflict (campaign_id, criterion) do update set state=excluded.state, evidence=excluded.evidence, reviewer_id=excluded.reviewer_id, updated_at=now() returning *""",
        (camp["id"], run["id"] if run else None, criterion, state, J(evidence or {}), user_id),
    ).fetchone()


def events_since(conn: psycopg.Connection, tenant_id: Any, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
    return conn.execute(
        """select e.* from campaign_event e join ingestion_campaign c on c.id=e.campaign_id where c.tenant_id=%s and e.id > %s order by e.id limit %s""",
        (tenant_id, after_id, limit),
    ).fetchall()


def delete(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any) -> dict[str, Any]:
    """Remove a campaign from the board without destroying its history.

    A hard delete is not available and should not be: audit_event, review_event and ingestion_job all reference
    the campaign, and audit_event is append-only, so erasing the row would mean erasing the record of who asked
    for what to be ingested. The row is retained and stops being listed.

    A campaign that has actually run is refused. Its jobs and any review decisions taken against them are real
    work that belongs on the board until it is explicitly cancelled, and quietly hiding it would hide those too.
    """
    row = conn.execute("select * from ingestion_campaign where id=%s and tenant_id=%s", (campaign_id, tenant_id)).fetchone()
    if not row:
        raise LookupError("no such campaign")
    if row["deleted_at"]:
        return {"id": str(row["id"]), "deleted": True, "already": True}

    runs = conn.execute("select count(*) as n from campaign_run where campaign_id=%s", (campaign_id,)).fetchone()["n"]
    if runs and row["control"] != "cancelled":
        raise ValueError("this campaign has already run; cancel it first, then delete it")

    conn.execute("update ingestion_campaign set deleted_at=now(), deleted_by=%s where id=%s", (user_id, campaign_id))
    _event(conn, campaign_id, None, "campaign.deleted", user_id, {"title": row["title"]})
    return {"id": str(campaign_id), "deleted": True, "already": False}
