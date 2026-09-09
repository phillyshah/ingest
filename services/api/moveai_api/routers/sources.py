from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from moveai_contracts.api import PERMISSION_OPS, IngestionJobCreate, SourceCreate
from moveai_db import J
from moveai_ingestion import queue as q
from moveai_ingestion.fetch import FetchError, canonicalize
from moveai_ingestion.pipeline import register_source_version

from ..auth import Principal, current_principal, require
from ..db import get_conn
from ..util import as_uuid, clean, clean_all, paginate

router = APIRouter(tags=["sources", "ingestion-jobs"])


@router.post("/sources", status_code=201)
def create_source(body: SourceCreate, p: Principal = Depends(require("source_admin")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    url = f"file://{body.local_path}" if body.local_path else body.canonical_url
    try:
        cu = canonicalize(url)
    except FetchError as e:
        raise HTTPException(422, {"code": e.error_class, "message": str(e)}) from e
    src = conn.execute(
        """insert into source(tenant_id, canonical_url, publisher, title, source_type, owner_user_id, allowlist_state, policy_reference, review_cadence_days, crawl_limits, intended_uses)
           values (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s) on conflict (canonical_url) do update set title=excluded.title returning *""",
        (p.tenant_id, cu, body.publisher, body.title, body.source_type, p.user_id, body.policy_reference, body.review_cadence_days, J(body.crawl_limits), body.intended_uses)).fetchone()
    svid = register_source_version(conn, src["id"], url=cu)
    r = body.rights
    cols = {op: (str(r.permissions.get(op, "unknown")) if r else "unknown") for op in PERMISSION_OPS}
    cols["can_train_model"] = "denied"
    conn.execute(f"insert into rights_grant(source_version_id, {','.join(cols)}, attribution_required, attribution_text, territory, expires_at, permission_evidence) "
                 f"values (%s,{','.join(['%s'] * len(cols))},%s,%s,%s,%s,%s)",
                 (svid, *cols.values(), r.attribution_required if r else None, r.attribution_text if r else None, r.territory if r else None,
                  r.expires_at if r else None, J(r.permission_evidence if r else {})))
    return {"id": str(src["id"]), "source_version_id": str(svid), "allowlist_state": src["allowlist_state"], "canonical_url": cu}


@router.get("/sources")
def list_sources(state: str | None = None, limit: int = Query(50, le=200), conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    rows = conn.execute(
        """select s.*, (select id from source_version v where v.source_id=s.id order by created_at desc limit 1) as latest_version_id,
                  (select pipeline_state from source_version v where v.source_id=s.id order by created_at desc limit 1) as latest_pipeline_state
             from source s where (%s::text is null or allowlist_state=%s) order by created_at desc limit %s""", (state, state, limit + 1)).fetchall()
    return paginate(clean_all(rows), limit)


@router.get("/sources/{source_id}")
def get_source(source_id: str, conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    src = conn.execute("select * from source where id=%s", (as_uuid(source_id),)).fetchone()
    if not src:
        raise HTTPException(404, {"code": "not_found", "message": "source not found"})
    versions = conn.execute("select * from source_version where source_id=%s order by created_at desc", (src["id"],)).fetchall()
    rights = conn.execute("select * from rights_grant where source_version_id = any(%s) order by created_at desc", ([v["id"] for v in versions],)).fetchall()
    return {**clean(src), "versions": clean_all(versions), "rights": clean_all(rights)}


@router.patch("/sources/{source_id}")
def patch_source(source_id: str, body: dict[str, Any], p: Principal = Depends(require("source_admin")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    allowed = {k: v for k, v in body.items() if k in ("allowlist_state", "publisher", "title", "policy_reference", "review_cadence_days", "next_review_at")}
    if not allowed:
        raise HTTPException(422, {"code": "no_changes", "message": "nothing to update"})
    if allowed.get("allowlist_state") not in (None, "pending", "approved", "denied"):
        raise HTTPException(422, {"code": "bad_state", "message": "allowlist_state must be pending|approved|denied"})
    sets = ", ".join(f"{k}=%s" for k in allowed)
    row = conn.execute(f"update source set {sets} where id=%s returning *", (*allowed.values(), as_uuid(source_id))).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not_found", "message": "source not found"})
    return clean(row)  # type: ignore[return-value]


@router.post("/ingestion-jobs", status_code=202)
def create_job(body: IngestionJobCreate, p: Principal = Depends(require("source_admin")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    src = conn.execute("select * from source where id=%s", (as_uuid(body.source_id),)).fetchone()
    if not src:
        raise HTTPException(404, {"code": "not_found", "message": "source not found"})
    sv = None if body.force_refetch else conn.execute("select * from source_version where source_id=%s and pipeline_state='discovered' order by created_at desc limit 1", (src["id"],)).fetchone()
    if sv is None:
        prior = conn.execute("select id from source_version where source_id=%s order by created_at desc limit 1", (src["id"],)).fetchone()
        svid = register_source_version(conn, src["id"], url=src["canonical_url"])
        if prior:
            g = conn.execute("select * from rights_grant where source_version_id=%s order by created_at desc limit 1", (prior["id"],)).fetchone()
            if g:
                cols = [k for k in g if k.startswith("can_")]
                conn.execute(f"insert into rights_grant(source_version_id, {','.join(cols)}, permission_evidence) values (%s,{','.join(['%s'] * len(cols))},%s)", (svid, *[g[k] for k in cols], J(g["permission_evidence"])))
    else:
        svid = sv["id"]
    job = q.enqueue(conn, stage="access_check", source_version_id=svid, payload={"url": src["canonical_url"], "region": "unknown"}, tenant_id=p.tenant_id,
                    campaign_id=as_uuid(body.campaign_id) if body.campaign_id else None, campaign_run_id=as_uuid(body.campaign_run_id) if body.campaign_run_id else None)
    return {"job_id": str(job["id"]), "source_version_id": str(svid), "state": job["state"]}


def _job_out(conn: psycopg.Connection, j: dict) -> dict[str, Any]:
    sv = conn.execute("select pipeline_state from source_version where id=%s", (j["source_version_id"],)).fetchone() if j["source_version_id"] else None
    return {**clean(j), "pipeline_state": sv["pipeline_state"] if sv else None, "retry_eligible": j["state"] in ("dead_letter", "failed") and j["error_class"] not in q.PERMANENT_ERRORS,
            "checkpoint": {k: v for k, v in (j["checkpoint"] or {}).items() if k != "result"}}


@router.get("/ingestion-jobs")
def list_jobs(state: str | None = None, campaign_id: str | None = None, run_id: str | None = None, limit: int = Query(50, le=500),
              conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    rows = conn.execute(
        """select * from ingestion_job where (%s::text is null or state::text=%s) and (%s::uuid is null or campaign_id=%s) and (%s::uuid is null or campaign_run_id=%s)
           order by created_at desc limit %s""", (state, state, campaign_id, campaign_id, run_id, run_id, limit + 1)).fetchall()
    return paginate([_job_out(conn, j) for j in rows], limit)


@router.get("/ingestion-jobs/{job_id}")
def get_job(job_id: str, conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    j = conn.execute("select * from ingestion_job where id=%s", (as_uuid(job_id),)).fetchone()
    if not j:
        raise HTTPException(404, {"code": "not_found", "message": "job not found"})
    out = _job_out(conn, j)
    out["events"] = clean_all(conn.execute("select * from job_event where job_id=%s order by id", (j["id"],)).fetchall())
    return out


@router.post("/ingestion-jobs/{job_id}/retry")
def retry_job(job_id: str, p: Principal = Depends(require("source_admin")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    if not q.retry_dead_letter(conn, as_uuid(job_id)):
        raise HTTPException(409, {"code": "not_retryable", "message": "job is not in a retryable state"})
    return {"job_id": job_id, "state": "queued"}
