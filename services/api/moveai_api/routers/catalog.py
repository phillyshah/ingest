from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from moveai_contracts.api import CatalogReleaseCreate, ProtocolCreate, ReviewCreate
from moveai_db import J
from moveai_planner.release import publish
from moveai_rules.ast import Action, parse_expr

from ..auth import Principal, current_principal, require
from ..db import get_conn
from ..reviews_service import decide, detail, queue
from ..util import as_uuid, clean, clean_all, paginate

router = APIRouter(tags=["catalog"])


@router.get("/exercises")
def search_exercises(
    q: str | None = None,
    region: str | None = None,
    condition: str | None = None,
    assistance: str | None = None,
    phase: str | None = None,
    equipment: str | None = None,
    setting: str | None = None,
    language: str | None = None,
    patient_display: bool | None = None,
    review_status: str | None = None,
    include_unpublished: bool = False,
    limit: int = Query(50, le=200),
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Structured search (spec §3C). Published-only by default for plan users; reviewers may include unpublished."""
    states = ["published"]
    if include_unpublished and p.has("pt", "clinical_lead", "source_admin", "rights_reviewer", "auditor"):
        states = [
            "draft",
            "pending_review",
            "approved",
            "published",
            "unsigned_placeholder",
            "invalidated",
            "rejected",
            "withdrawn",
            "superseded",
        ]
    if review_status:
        states = [review_status]
    rows = conn.execute(
        """select v.*, c.preferred_name as concept_name, c.aliases,
                  (select json_agg(json_build_object('id', m.id, 'media_state', m.media_state)) from media_asset_version m where m.variant_version_id=v.id) as media,
                  (select json_agg(json_build_object('id', u.id, 'phase', u.phase, 'condition_id', u.condition_id, 'approval_state', u.approval_state, 'directness', u.directness,
                                                     'support_category', u.support_category)) from clinical_use_version u where u.variant_version_id=v.id) as uses
             from exercise_variant_version v join exercise_concept c on c.id=v.concept_id
            where v.approval_state::text = any(%s)
              and (%s::text is null or v.name ilike '%%'||%s||'%%' or c.preferred_name ilike '%%'||%s||'%%' or exists (select 1 from unnest(c.aliases) a where a ilike '%%'||%s||'%%'))
              and (%s::text is null or v.region=%s) and (%s::text is null or v.assistance::text=%s)
              and (%s::text is null or %s = any(v.equipment)) and (%s::text is null or %s = any(v.setting)) and (%s::text is null or %s = any(v.supported_languages))
              and (%s::text is null or exists (select 1 from clinical_use_version u join condition cd on cd.id=u.condition_id where u.variant_version_id=v.id and (cd.internal_code=%s or cd.preferred_name ilike '%%'||%s||'%%')))
              and (%s::text is null or exists (select 1 from clinical_use_version u where u.variant_version_id=v.id and u.phase=%s))
            order by v.name limit %s""",
        (
            states,
            q,
            q,
            q,
            q,
            region,
            region,
            assistance,
            assistance,
            equipment,
            equipment,
            setting,
            setting,
            language,
            language,
            condition,
            condition,
            condition,
            phase,
            phase,
            limit + 1,
        ),
    ).fetchall()
    out = []
    for r in rows:
        media = r["media"] or []
        item = clean(r)
        item["media_quality"] = {
            "graphic_available": any(m["media_state"] == "graphic_available" for m in media),
            "states": [m["media_state"] for m in media],
        }
        item["clinical_confidence"] = {
            "uses": len(r["uses"] or []),
            "directness": sorted({u["directness"] for u in (r["uses"] or [])}),
            "support": sorted({u["support_category"] for u in (r["uses"] or []) if u["support_category"]}),
        }
        if patient_display is not None:
            ok = any(m["media_state"] == "graphic_available" for m in media)
            if ok != patient_display:
                continue
        out.append(item)
    return paginate(out, limit)


@router.get("/exercises/{entity_id}/versions/{version}")
def get_version(
    entity_id: str,
    version: int,
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    v = conn.execute(
        "select * from exercise_variant_version where entity_id=%s and version=%s",
        (as_uuid(entity_id), version),
    ).fetchone()
    if not v:
        raise HTTPException(404, {"code": "not_found", "message": "version not found"})
    if v["approval_state"] != "published" and not p.has("pt", "clinical_lead", "source_admin", "rights_reviewer", "auditor"):
        raise HTTPException(404, {"code": "not_found", "message": "version not published"})
    return detail(conn, "exercise_variant_version", v["id"])


@router.get("/exercises/{entity_id}")
def get_entity(entity_id: str, conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    vs = conn.execute(
        "select * from exercise_variant_version where entity_id=%s order by version desc",
        (as_uuid(entity_id),),
    ).fetchall()
    if not vs:
        raise HTTPException(404, {"code": "not_found", "message": "exercise not found"})
    return {
        "entity_id": entity_id,
        "latest": detail(conn, "exercise_variant_version", vs[0]["id"]),
        "versions": clean_all(vs),
    }


@router.get("/reviews/queue")
def review_queue(
    entity_table: str | None = None,
    campaign_id: str | None = None,
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(require("pt", "clinical_lead", "rights_reviewer", "auditor")),
) -> dict[str, Any]:
    return queue(conn, p, table=entity_table, campaign_id=campaign_id, limit=limit, cursor=cursor)


@router.get("/reviews/{entity_table}/{version_id}")
def review_detail(
    entity_table: str,
    version_id: str,
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(require("pt", "clinical_lead", "rights_reviewer", "auditor", "source_admin")),
) -> dict[str, Any]:
    if entity_table not in (
        "exercise_variant_version",
        "clinical_use_version",
        "protocol_version",
        "rule_version",
        "media_asset_version",
        "rights_grant",
        "source_version",
    ):
        raise HTTPException(422, {"code": "bad_table", "message": "unsupported entity table"})
    return detail(conn, entity_table, as_uuid(version_id))


@router.post("/reviews", status_code=201)
def create_review(
    body: ReviewCreate,
    p: Principal = Depends(current_principal),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return decide(conn, p, body)


@router.post("/protocols", status_code=201)
def create_protocol(
    body: ProtocolCreate,
    p: Principal = Depends(require("pt", "clinical_lead")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Save a draft pathway with typed criteria and provenance (spec §3D). Unsupported rule constructs cannot be saved."""
    import uuid as _uuid

    cond = conn.execute("select id from condition where internal_code=%s", (body.condition_code,)).fetchone()
    if not cond:
        raise HTTPException(422, {"code": "unknown_condition", "message": "condition_code not found"})
    rule_ids = []
    previews = []
    for r in body.rules:
        try:
            expr = parse_expr(r["expression"])
            action = Action.model_validate(r["action"])
        except (ValueError, KeyError) as e:
            raise HTTPException(422, {"code": "unsupported_rule", "message": str(e)}) from e
        rid = conn.execute(
            """insert into rule_version(entity_id, version, tenant_id, name, rule_kind, expression, required_inputs, action, severity, rationale, author_id, approval_state)
               values (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft') returning id""",
            (
                _uuid.uuid4(),
                p.tenant_id if body.tenant_private else None,
                r.get("name", r.get("key", "rule")),
                r.get("kind", "eligibility"),
                J(expr.model_dump(by_alias=True, exclude_none=True)),
                sorted(expr.fields()),
                J({**action.model_dump(exclude_none=True), "key": r.get("key")}),
                r.get("severity", "info"),
                r.get("rationale"),
                p.user_id,
            ),
        ).fetchone()["id"]
        rule_ids.append(rid)
        previews.append(
            f"IF {_readable(expr.model_dump(by_alias=True, exclude_none=True))} THEN {action.type} {action.message or action.field or action.fields or ''}".strip()
        )
    for ph in body.phases:
        for uid in ph.get("items", []):
            u = conn.execute("select approval_state from clinical_use_version where id=%s", (uid,)).fetchone()
            if not u or u["approval_state"] not in ("approved", "published"):
                raise HTTPException(
                    422,
                    {
                        "code": "unapproved_item",
                        "message": f"clinical use {uid} is not approved; pathways use approved variants only",
                    },
                )
    row = conn.execute(
        """insert into protocol_version(entity_id, version, tenant_id, condition_id, name, population_description, author_id, provenance, setting, required_inputs, phases,
             monitoring, rule_version_ids, recovery_horizon, approval_state)
           values (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft') returning id""",
        (
            _uuid.uuid4(),
            p.tenant_id if body.tenant_private else None,
            cond["id"],
            body.name,
            body.population_description,
            p.user_id,
            J(
                {
                    **body.provenance,
                    "kind": body.provenance.get("kind", "clinician_authored"),
                    "pack_rules": [str(r) for r in rule_ids],
                }
            ),
            body.setting,
            body.required_inputs,
            J(body.phases),
            J(body.monitoring),
            rule_ids,
            J(body.recovery_horizon) if body.recovery_horizon else None,
        ),
    ).fetchone()
    for ph in body.phases:
        for i, uid in enumerate(ph.get("items", [])):
            conn.execute(
                "insert into protocol_item(protocol_version_id, phase_key, clinical_use_version_id, position) values (%s,%s,%s,%s)",
                (row["id"], ph["key"], uid, i),
            )
    return {
        "protocol_version_id": str(row["id"]),
        "rule_version_ids": [str(r) for r in rule_ids],
        "rule_previews": previews,
        "approval_state": "draft",
        "note": "the author cannot approve their own pathway; a clinical lead approves publication",
    }


def _readable(e: dict[str, Any]) -> str:
    op = e["op"]
    if op in ("and", "or"):
        return "(" + f" {op.upper()} ".join(_readable(a) for a in e["args"]) + ")"
    if op == "not":
        return "NOT " + _readable(e["arg"])
    if op == "known":
        return f"{e['field']} is known"
    if op == "status":
        return f"{e['field']} is {e['is']}"
    if op == "between":
        return f"{e['field']} in [{e.get('min')}, {e.get('max')}]"
    if op in ("true", "false"):
        return op.upper()
    return f"{e['field']} {op} {e.get('value')!r}"


@router.get("/protocols")
def list_protocols(
    condition: str | None = None,
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    rows = conn.execute(
        """select p.id, p.entity_id, p.version, p.name, p.approval_state, p.content_pack, p.content_pack_version, p.required_inputs, p.phases, p.provenance, p.recovery_horizon,
                  c.internal_code, c.preferred_name as condition_name from protocol_version p join condition c on c.id=p.condition_id
            where (%s::text is null or c.internal_code=%s) and (p.tenant_id is null or p.tenant_id=%s) order by c.preferred_name, p.name""",
        (condition, condition, p.tenant_id),
    ).fetchall()
    return {"items": clean_all(rows), "total": len(rows)}


@router.post("/catalog-releases", status_code=201)
def create_release(
    body: CatalogReleaseCreate,
    p: Principal = Depends(require("clinical_lead")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if conn.execute("select 1 from catalog_release where label=%s", (body.label,)).fetchone():
        raise HTTPException(409, {"code": "label_exists", "message": "release label already used"})
    return publish(conn, label=body.label, published_by=p.user_id, tables=body.include_tables)


@router.get("/catalog-releases")
def list_releases(conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    rows = conn.execute(
        "select id, label, manifest_sha256, published_at, prior_release_id, jsonb_array_length(manifest) as item_count from catalog_release order by published_at desc"
    ).fetchall()
    return {"items": clean_all(rows), "total": len(rows)}


@router.get("/changes")
def changes(
    cursor: int = 0,
    limit: int = Query(100, le=1000),
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    rows = conn.execute("select * from catalog_change where cursor > %s order by cursor limit %s", (cursor, limit + 1)).fetchall()
    page = clean_all(rows[:limit])
    return {"items": page, "next_cursor": str(page[-1]["cursor"]) if len(rows) > limit else None}


@router.get("/conditions")
def conditions(conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    rows = conn.execute("select * from condition order by preferred_name").fetchall()
    return {"items": clean_all(rows)}


@router.get("/terminology/resolve")
def resolve(
    code: str,
    service_date: str | None = None,
    conn: psycopg.Connection = Depends(get_conn),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    from datetime import date

    from moveai_ingestion.terminology import resolve_code

    return resolve_code(conn, code, date.fromisoformat(service_date) if service_date else date.today())
