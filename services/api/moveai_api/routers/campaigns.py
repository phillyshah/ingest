from __future__ import annotations

import asyncio
import json
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from moveai_contracts.api import CampaignCreate, CampaignPatch, CampaignScopeInput, RunAction
from moveai_db import connect
from moveai_ingestion import campaigns as svc
from moveai_ingestion.campaigns import CampaignError
from sse_starlette.sse import EventSourceResponse

from ..auth import Principal, current_principal, require
from ..db import get_conn
from ..util import as_uuid, clean_all

router = APIRouter(prefix="/ingestion-campaigns", tags=["campaigns"])


def _tenant(p: Principal) -> str:
    if not p.tenant_id:
        raise HTTPException(403, {"code": "no_tenant", "message": "campaigns require a tenant context"})
    return p.tenant_id


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except CampaignError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from e


@router.post("", status_code=201)
def create(
    body: CampaignCreate,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    data = body.model_dump(mode="json")
    return _wrap(svc.create, conn, _tenant(p), p.user_id, data)


@router.get("")
def list_campaigns(
    lifecycle: str | None = None,
    control: str | None = None,
    include_archived: bool = False,
    p: Principal = Depends(current_principal),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    items = svc.list_cards(conn, _tenant(p), lifecycle=lifecycle, control=control, include_archived=include_archived)
    return {
        "items": items,
        "total": len(items),
        "server_time": conn.execute("select now() as t").fetchone()["t"],
    }


@router.get("/stream")
async def stream(request: Request, after: int = 0, p: Principal = Depends(current_principal)):
    """Server-sent events: campaign events since `after`. Clients reconnect with the last id (spec §21F)."""
    tenant = _tenant(p)

    async def gen():
        last = after
        while not await request.is_disconnected():
            with connect() as c:
                rows = svc.events_since(c, tenant, last)
                c.commit()
            for r in rows:
                last = r["id"]
                yield {
                    "id": str(r["id"]),
                    "event": "campaign",
                    "data": json.dumps(
                        {
                            "campaign_id": str(r["campaign_id"]),
                            "run_id": str(r["run_id"]) if r["run_id"] else None,
                            "event": r["event"],
                            "detail": r["detail"],
                            "at": r["created_at"].isoformat(),
                        }
                    ),
                }
            yield {"event": "heartbeat", "data": json.dumps({"last_id": last})}
            await asyncio.sleep(2)

    return EventSourceResponse(gen(), ping=15)


@router.get("/dashboard")
def dashboard(p: Principal = Depends(current_principal), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    cards = svc.list_cards(conn, _tenant(p), include_archived=True)
    by = {}
    for c in cards:
        by[c["lifecycle"]] = by.get(c["lifecycle"], 0) + 1
    pt_backlog = conn.execute("select count(*) as n from exercise_variant_version where approval_state='pending_review'").fetchone()["n"]
    published = conn.execute("select count(*) as n from protocol_version where approval_state='published'").fetchone()["n"]
    spend = conn.execute("select coalesce(sum(cost_usd),0) as c from ingestion_job where created_at > now() - interval '1 day'").fetchone()[
        "c"
    ]
    stale = [c["id"] for c in cards if c["heartbeat_stale"]]
    oldest = conn.execute("select min(created_at) as t from exercise_variant_version where approval_state='pending_review'").fetchone()["t"]
    rights_holds = conn.execute("select count(*) as n from source_version where pipeline_state='rights_hold'").fetchone()["n"]
    failures = conn.execute("select count(*) as n from ingestion_job where state in ('dead_letter','failed')").fetchone()["n"]
    return {
        "active_campaigns": by.get("running", 0) + by.get("queued", 0),
        "needs_admin_action": by.get("needs_attention", 0),
        "by_lifecycle": by,
        "pt_backlog": pt_backlog,
        "oldest_pending_review": oldest,
        "published_pathways": published,
        "spend_today_usd": float(spend),
        "stale_runs": stale,
        "rights_holds": rights_holds,
        "extraction_failures": failures,
    }


@router.get("/events-all")
def events_all(
    after: int = 0,
    limit: int = Query(200, le=1000),
    p: Principal = Depends(current_principal),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Polling fallback for the board when the client cannot use EventSource (dev header shim)."""
    rows = svc.events_since(conn, _tenant(p), after, limit)
    return {"items": clean_all(rows), "next_cursor": str(rows[-1]["id"]) if rows else None}


@router.post("/preview-unsaved")
def preview_unsaved(
    body: CampaignScopeInput,
    p: Principal = Depends(require("source_admin", "clinical_lead", "pt")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Scope preview for the new-campaign form before anything is saved (spec §21B)."""
    return svc.scope_preview(conn, _tenant(p), body.model_dump(mode="json"))


@router.get("/{campaign_id}")
def get(campaign_id: str, p: Principal = Depends(current_principal), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    return _wrap(svc.get, conn, _tenant(p), as_uuid(campaign_id))


@router.patch("/{campaign_id}")
def patch(
    campaign_id: str,
    body: CampaignPatch,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    cid = as_uuid(campaign_id)
    camp = _wrap(svc._camp, conn, _tenant(p), cid)
    sets = {
        k: v
        for k, v in body.model_dump(exclude_none=True).items()
        if k in ("title", "priority", "reviewer_id", "owner_id", "maintenance_enabled")
    }
    if sets:
        conn.execute(
            "update ingestion_campaign set " + ", ".join(f"{k}=%s" for k in sets) + " where id=%s",
            (*sets.values(), camp["id"]),
        )
        svc._event(conn, camp["id"], None, "edited", p.user_id, sets)
    if body.scope is not None:
        return _wrap(svc.revise_scope, conn, _tenant(p), p.user_id, cid, body.scope.model_dump(mode="json"))
    return _wrap(svc.get, conn, _tenant(p), cid)


@router.delete("/{campaign_id}")
def delete(
    campaign_id: str,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Remove a campaign from the board. Soft: the row and its audit trail are retained (see svc.delete)."""
    try:
        return svc.delete(conn, _tenant(p), p.user_id, as_uuid(campaign_id))
    except LookupError as e:
        raise HTTPException(404, {"code": "not_found", "message": str(e)}) from e
    except ValueError as e:
        raise HTTPException(409, {"code": "campaign_has_run", "message": str(e)}) from e


@router.post("/{campaign_id}/scope-preview")
def scope_preview(
    campaign_id: str,
    body: CampaignScopeInput | None = None,
    p: Principal = Depends(require("source_admin", "clinical_lead", "pt")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    camp = _wrap(svc._camp, conn, _tenant(p), as_uuid(campaign_id))
    if body is None:
        sv = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
        scope = {
            "ailment_text": sv["ailment_text"],
            "codes": [c["code"] for c in sv["codes"]],
            "limits": sv["limits"],
            "supplied_source_urls": sv["supplied_source_urls"],
            "source_policy": sv["source_policy"],
            "scope_confirmed": bool(sv["authorized_by"]),
            "exclusion": sv["exclusion"],
            "refinements": sv["refinements"],
        }
    else:
        scope = body.model_dump(mode="json")
    return svc.scope_preview(conn, _tenant(p), scope)


@router.post("/{campaign_id}/runs", status_code=202)
def start_run(
    campaign_id: str,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _wrap(svc.start, conn, _tenant(p), p.user_id, as_uuid(campaign_id))


@router.post("/{campaign_id}/pause")
def pause(
    campaign_id: str,
    body: RunAction,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _wrap(svc.pause, conn, _tenant(p), p.user_id, as_uuid(campaign_id), body.expected_revision)


@router.post("/{campaign_id}/resume")
def resume(
    campaign_id: str,
    body: RunAction,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _wrap(svc.resume, conn, _tenant(p), p.user_id, as_uuid(campaign_id), body.expected_revision)


@router.post("/{campaign_id}/cancel")
def cancel(
    campaign_id: str,
    body: RunAction,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _wrap(svc.cancel, conn, _tenant(p), p.user_id, as_uuid(campaign_id), body.expected_revision, body.reason)


@router.post("/{campaign_id}/retry-failed")
def retry_failed(
    campaign_id: str,
    body: RunAction,
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return _wrap(svc.retry_failed, conn, _tenant(p), p.user_id, as_uuid(campaign_id), body.expected_revision)


@router.post("/{campaign_id}/transition")
def transition(
    campaign_id: str,
    body: dict[str, Any],
    p: Principal = Depends(require("source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Drag-and-drop target. Only Draft->Queued (start) and priority changes are permitted transitions; everything else is refused (spec §21C)."""
    target = body.get("to")
    if target == "queued":
        return _wrap(svc.start, conn, _tenant(p), p.user_id, as_uuid(campaign_id))
    raise HTTPException(
        409,
        {
            "code": "transition_not_permitted",
            "message": f"cards cannot be moved to {target!r}; lifecycle is derived from real events",
        },
    )


@router.post("/{campaign_id}/coverage")
def coverage(
    campaign_id: str,
    body: dict[str, Any],
    p: Principal = Depends(require("pt", "clinical_lead")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if body.get("state") not in ("unknown", "unmet", "met", "not_applicable"):
        raise HTTPException(422, {"code": "bad_state", "message": "state must be unknown|unmet|met|not_applicable"})
    row = _wrap(
        svc.set_coverage,
        conn,
        _tenant(p),
        p.user_id,
        as_uuid(campaign_id),
        body["criterion"],
        body["state"],
        body.get("evidence"),
    )
    return {k: str(v) if k.endswith("id") and v else v for k, v in row.items()}


@router.get("/{campaign_id}/items")
def items(
    campaign_id: str,
    disposition: str | None = None,
    limit: int = Query(100, le=1000),
    p: Principal = Depends(current_principal),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    _wrap(svc._camp, conn, _tenant(p), as_uuid(campaign_id))
    rows = conn.execute(
        "select * from campaign_item where campaign_id=%s and (%s::text is null or disposition=%s) order by created_at limit %s",
        (campaign_id, disposition, disposition, limit),
    ).fetchall()
    return {"items": clean_all(rows), "total": len(rows)}


@router.get("/{campaign_id}/events")
def events(
    campaign_id: str,
    after: int = 0,
    limit: int = Query(200, le=1000),
    p: Principal = Depends(current_principal),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    _wrap(svc._camp, conn, _tenant(p), as_uuid(campaign_id))
    rows = conn.execute(
        "select * from campaign_event where campaign_id=%s and id > %s order by id limit %s",
        (campaign_id, after, limit),
    ).fetchall()
    return {"items": clean_all(rows), "next_cursor": str(rows[-1]["id"]) if rows else None}


@router.get("/{campaign_id}/costs")
def costs(campaign_id: str, p: Principal = Depends(current_principal), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    _wrap(svc._camp, conn, _tenant(p), as_uuid(campaign_id))
    ledger = conn.execute(
        "select l.* from budget_ledger l join campaign_run r on r.id=l.run_id where r.campaign_id=%s order by l.id",
        (campaign_id,),
    ).fetchall()
    jobs = conn.execute(
        "select stage, state, count(*) as n, coalesce(sum(cost_usd),0) as usd, coalesce(sum(duration_ms),0) as ms from ingestion_job where campaign_id=%s group by stage, state order by stage",
        (campaign_id,),
    ).fetchall()
    return {"ledger": clean_all(ledger), "by_stage": [{**j, "usd": float(j["usd"])} for j in jobs]}


@router.get("/{campaign_id}/exercises")
def exercises(campaign_id: str, p: Principal = Depends(current_principal), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    _wrap(svc._camp, conn, _tenant(p), as_uuid(campaign_id))
    rows = conn.execute(
        """select v.id, v.entity_id, v.version, v.name, v.assistance, v.approval_state, v.duplicate_of_entity_id, v.source_reference, v.extraction_warnings, d.upstream_id as source_version_id
             from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id and d.upstream_table='source_version'
             join campaign_item ci on ci.item_id=d.upstream_id and ci.item_table='source_version' where ci.campaign_id=%s order by v.created_at""",
        (campaign_id,),
    ).fetchall()
    claims = conn.execute(
        "select ec.id, ec.claim_type, ec.paraphrase, ec.locator, ec.source_version_id, ec.ambiguity_flags from evidence_claim ec join campaign_item ci on ci.item_id=ec.source_version_id and ci.item_table='source_version' where ci.campaign_id=%s order by ec.created_at",
        (campaign_id,),
    ).fetchall()
    return {"variants": clean_all(rows), "claims": clean_all(claims)}
