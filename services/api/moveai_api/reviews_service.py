"""Version-specific clinical and rights decisions (spec §3B, §11 roles, §13 #5/#8/#14)."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import HTTPException
from moveai_contracts.api import PERMISSION_OPS, ReviewCreate
from moveai_db import J
from moveai_planner.approval import propagate_withdrawal

from .auth import Principal

CLINICAL_TABLES = (
    "exercise_variant_version",
    "clinical_use_version",
    "media_asset_version",
    "diagnosis_mapping_version",
    "population_applicability_version",
)
LEAD_ONLY_TABLES = ("protocol_version", "rule_version")
EDITABLE = {
    "exercise_variant_version": [
        "name",
        "region",
        "joint",
        "movement_plane",
        "target_impairment",
        "functional_goal",
        "starting_position",
        "assistance",
        "chain",
        "load_mode",
        "side_behavior",
        "equipment",
        "balance_demand",
        "accessibility_notes",
        "setting",
        "step_sequence",
        "breathing_cues",
        "common_errors",
        "cues",
        "supported_modifications",
        "range_constraints",
        "source_reference",
    ],
    "clinical_use_version": [
        "presentation",
        "indication",
        "exclusion",
        "phase",
        "goals",
        "dose_envelope",
        "directness",
        "support_category",
        "clinician_rationale",
        "supporting_claim_ids",
    ],
}


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": msg})


def _row(conn: psycopg.Connection, table: str, vid: Any) -> dict:
    r = conn.execute(f"select * from {table} where id=%s", (vid,)).fetchone()
    if not r:
        raise _err(404, "not_found", f"{table} {vid} not found")
    return r


def _authorize(p: Principal, body: ReviewCreate, row: dict) -> str:
    d = body.decision
    if d in ("rights_allow", "rights_deny"):
        if not p.has("rights_reviewer", "clinical_lead"):
            raise _err(403, "forbidden", "rights decisions require rights_reviewer")
        return "rights_reviewer"
    if body.entity_table in LEAD_ONLY_TABLES and d in ("approve", "accept"):
        if not p.has("clinical_lead"):
            raise _err(403, "forbidden", "protocol/rule approval requires clinical_lead")
    elif not p.has("pt", "clinical_lead"):
        raise _err(403, "forbidden", "clinical decisions require pt or clinical_lead")
    author = row.get("created_by") or row.get("author_id")
    if d in ("approve", "accept") and author and str(author) == p.user_id:
        raise _err(403, "self_approval", "the author cannot approve their own version")
    return "clinical_lead" if p.has("clinical_lead") else "pt"


def decide(conn: psycopg.Connection, p: Principal, body: ReviewCreate) -> dict[str, Any]:
    table = body.entity_table
    row = _row(conn, table, body.version_id)
    role = _authorize(p, body, row)
    if body.expected_approval_state and row.get("approval_state") and row["approval_state"] != body.expected_approval_state:
        raise _err(409, "stale_state", f"version is {row['approval_state']}, expected {body.expected_approval_state}")
    out: dict[str, Any] = {
        "entity_table": table,
        "version_id": str(row["id"]),
        "new_version_id": None,
        "invalidated_prior_approval": False,
    }
    d = body.decision
    if d in ("rights_allow", "rights_deny"):
        if table != "rights_grant":
            raise _err(422, "wrong_table", "rights decisions target rights_grant")
        ops = [k for k in (body.changes or {}) if k in PERMISSION_OPS] or list(PERMISSION_OPS)
        state = "allowed" if d == "rights_allow" else "denied"
        sets = ", ".join(f"{op}=%s" for op in ops)
        conn.execute(
            f"update rights_grant set {sets}, rights_reviewer_id=%s, reviewed_at=now(), permission_evidence = permission_evidence || %s where id=%s",
            (
                *[state] * len(ops),
                p.user_id,
                J({"decision": d, "reason": body.reason, "ops": ops}),
                row["id"],
            ),
        )
        if d == "rights_deny":
            for m in conn.execute("select id from media_asset_version where rights_grant_id=%s", (row["id"],)).fetchall():
                conn.execute(
                    "update media_asset_version set media_state='rights_hold' where id=%s and approval_state not in ('approved','published')",
                    (m["id"],),
                )
                conn.execute(
                    "insert into outbox_event(event_type, object_table, object_id, payload) values ('rights.expired','media_asset_version',%s,%s)",
                    (m["id"], J({"reason": body.reason})),
                )
        out["approval_state"] = row.get("approval_state") or "n/a"
    elif d in ("approve", "accept"):
        if row["approval_state"] not in ("draft", "pending_review"):
            raise _err(409, "not_reviewable", f"cannot approve a {row['approval_state']} version")
        if table == "exercise_variant_version" and row["duplicate_of_entity_id"]:
            raise _err(
                409,
                "duplicate_unresolved",
                "resolve the duplicate proposal (merge or reject) before approval",
            )
        conn.execute(
            f"update {table} set approval_state='approved'"
            + (
                ", approved_by=%s, approved_at=now()"
                if table in ("exercise_variant_version", "clinical_use_version", "protocol_version")
                else ""
            )
            + (", approver_id=%s" if table == "rule_version" else "")
            + " where id=%s",
            (
                (p.user_id, row["id"])
                if table in ("exercise_variant_version", "clinical_use_version", "protocol_version", "rule_version")
                else (row["id"],)
            ),
        )
        # supersede prior versions of the same entity
        if "entity_id" in row:
            conn.execute(
                f"update {table} set approval_state='superseded', superseded_by_id=%s where entity_id=%s and id<>%s and approval_state in ('approved','published')",
                (row["id"], row["entity_id"], row["id"]),
            )
        out["approval_state"] = "approved"
    elif d == "edit":
        if table not in EDITABLE:
            raise _err(422, "not_editable", f"{table} edits are not supported via review")
        changes = {k: v for k, v in (body.changes or {}).items() if k in EDITABLE[table]}
        if not changes:
            raise _err(422, "no_changes", "no editable clinical fields in changes")
        cols = [
            c
            for c in row
            if c
            not in (
                "id",
                "version",
                "prior_version_id",
                "approval_state",
                "approved_by",
                "approved_at",
                "invalidated_at",
                "withdrawn_at",
                "withdrawal_reason",
                "superseded_by_id",
                "created_at",
                "updated_at",
                "created_by",
            )
        ]
        data = {c: row[c] for c in cols}
        data.update(changes)
        nxt = conn.execute(f"select coalesce(max(version),0)+1 as v from {table} where entity_id=%s", (row["entity_id"],)).fetchone()["v"]
        vals = [
            J(v)
            if isinstance(v, dict | list)
            and c
            in (
                "step_sequence",
                "supported_modifications",
                "range_constraints",
                "source_reference",
                "extraction_warnings",
                "presentation",
                "dose_envelope",
            )
            else v
            for c, v in data.items()
        ]
        new = conn.execute(
            f"insert into {table}({','.join(data)}, version, prior_version_id, approval_state, created_by) values ({','.join(['%s'] * len(data))},%s,%s,'pending_review',%s) returning id",
            (*vals, nxt, row["id"], p.user_id),
        ).fetchone()
        if row["approval_state"] in ("approved", "published"):
            conn.execute(
                f"update {table} set approval_state='invalidated', invalidated_at=now(), superseded_by_id=%s where id=%s",
                (new["id"], row["id"]),
            )
            out["invalidated_prior_approval"] = True
            conn.execute(
                "insert into catalog_change(change_type, entity_table, version_id) values ('superseded',%s,%s)",
                (table, row["id"]),
            )
        out["new_version_id"] = str(new["id"])
        out["approval_state"] = "pending_review"
    elif d == "split":
        parts = (body.changes or {}).get("parts") or []
        if len(parts) < 2 or table not in EDITABLE:
            raise _err(422, "bad_split", "split needs changes.parts with at least two variant definitions")
        ids = []
        for part in parts:
            sub = ReviewCreate(
                entity_table=table,
                version_id=body.version_id,
                decision="edit",
                changes=part,
                reason=body.reason,
            )
            ids.append(decide(conn, p, sub)["new_version_id"])
        conn.execute(
            f"update {table} set approval_state='rejected' where id=%s and approval_state in ('draft','pending_review')",
            (row["id"],),
        )
        out["new_version_id"] = ids[0]
        out["split_version_ids"] = ids
        out["approval_state"] = "rejected"
    elif d == "merge":
        if table != "exercise_variant_version" or not body.merge_into_entity_id:
            raise _err(422, "bad_merge", "merge targets exercise_variant_version and needs merge_into_entity_id")
        target = conn.execute(
            "select * from exercise_variant_version where entity_id=%s order by version desc limit 1",
            (body.merge_into_entity_id,),
        ).fetchone()
        if not target:
            raise _err(404, "not_found", "merge target not found")
        for f in ("assistance", "load_mode", "setting", "range_constraints", "starting_position"):
            if row[f] != target[f] and not (f == "setting" and sorted(row[f] or []) == sorted(target[f] or [])):
                raise _err(
                    409,
                    "distinct_variant",
                    f"cannot merge: {f} differs ({row[f]!r} vs {target[f]!r}); these are distinct variants",
                )
        conn.execute(
            "update exercise_variant_version set approval_state='superseded', superseded_by_id=%s, duplicate_of_entity_id=%s where id=%s",
            (target["id"], target["entity_id"], row["id"]),
        )
        conn.execute(
            "insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('exercise_variant_version',%s,'exercise_variant_version',%s,'derived_from') on conflict do nothing",
            (row["id"], target["id"]),
        )
        out["approval_state"] = "superseded"
        out["merged_into_version_id"] = str(target["id"])
    elif d == "reject":
        conn.execute(
            f"update {table} set approval_state='rejected' where id=%s and approval_state in ('draft','pending_review','unsigned_placeholder')",
            (row["id"],),
        )
        out["approval_state"] = "rejected"
    elif d == "request_clarification":
        out["approval_state"] = row["approval_state"]
    elif d == "withdraw":
        if not p.has("clinical_lead", "rights_reviewer"):
            raise _err(403, "forbidden", "withdrawal requires clinical_lead or rights_reviewer")
        if table == "source_version":
            conn.execute(
                "update source_version set pipeline_state='withdrawn', withdrawn_at=now(), withdrawal_reason=%s where id=%s",
                (body.reason, row["id"]),
            )
        else:
            conn.execute(
                f"update {table} set approval_state='withdrawn', withdrawn_at=now(), withdrawal_reason=%s where id=%s",
                (body.reason, row["id"]),
            )
        out["propagation"] = propagate_withdrawal(
            conn, entity_table=table, version_id=row["id"], reason=body.reason or "withdrawn by reviewer"
        )
        out["approval_state"] = "withdrawn"
    ev = conn.execute(
        "insert into review_event(tenant_id, entity_table, version_id, reviewer_id, reviewer_role, decision, changes, reason, time_spent_seconds, campaign_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (
            p.tenant_id,
            table,
            row["id"],
            p.user_id,
            role,
            d.value if hasattr(d, "value") else d,
            J(body.changes) if body.changes else None,
            body.reason,
            body.time_spent_seconds,
            body.campaign_id,
        ),
    ).fetchone()
    out["review_event_id"] = str(ev["id"])
    return out


def queue(
    conn: psycopg.Connection,
    p: Principal,
    *,
    table: str | None = None,
    campaign_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    tables = [table] if table else ["exercise_variant_version", "clinical_use_version", "protocol_version", "rule_version"]
    for t in tables:
        sql = f"select * from {t} where approval_state = 'pending_review'"
        params: list[Any] = []
        if campaign_id and t == "exercise_variant_version":
            sql += " and id in (select d.downstream_id from dependency_edge d join campaign_item ci on ci.item_id=d.upstream_id and ci.item_table='source_version' where ci.campaign_id=%s)"
            params.append(campaign_id)
        if cursor:
            sql += " and created_at > (select created_at from " + t + " where id=%s)"
            params.append(cursor)
        sql += " order by created_at limit %s"
        params.append(limit)
        for r in conn.execute(sql, params).fetchall():
            items.append(
                {
                    "entity_table": t,
                    "version_id": str(r["id"]),
                    "name": r.get("name") or r.get("indication") or r.get("phase"),
                    "created_at": r["created_at"],
                    "flags": r.get("extraction_warnings") or [],
                    "duplicate_of_entity_id": str(r["duplicate_of_entity_id"]) if r.get("duplicate_of_entity_id") else None,
                }
            )
    items.sort(key=lambda x: x["created_at"])
    return {
        "items": items[:limit],
        "next_cursor": items[limit - 1]["version_id"] if len(items) > limit else None,
        "total": len(items),
    }


def detail(conn: psycopg.Connection, table: str, vid: Any) -> dict[str, Any]:
    row = _row(conn, table, vid)
    out: dict[str, Any] = {"entity_table": table, "record": _clean(row)}
    src = None
    sv = None
    edge = conn.execute(
        "select upstream_id from dependency_edge where downstream_table=%s and downstream_id=%s and upstream_table='source_version'",
        (table, vid),
    ).fetchone()
    if edge:
        sv = conn.execute(
            "select sv.*, s.canonical_url, s.publisher, s.title, s.source_type from source_version sv join source s on s.id=sv.source_id where sv.id=%s",
            (edge["upstream_id"],),
        ).fetchone()
        grant = conn.execute(
            "select * from rights_grant where source_version_id=%s order by created_at desc limit 1",
            (sv["id"],),
        ).fetchone()
        src = {
            "source_version_id": str(sv["id"]),
            "url": sv["canonical_url"],
            "final_url": sv["final_url"],
            "publisher": sv["publisher"],
            "title": sv["title"],
            "document_identity": sv["document_identity"],
            "retrieved_at": sv["retrieved_at"],
            "pipeline_state": sv["pipeline_state"],
            "excerpt_allowed": bool(grant and grant["can_store_excerpt"] == "allowed"),
            "rights": _clean(grant) if grant else None,
        }
    claims = []
    if sv:
        claims = [
            _clean(c)
            for c in conn.execute(
                "select * from evidence_claim where source_version_id=%s order by claim_type, created_at",
                (sv["id"],),
            ).fetchall()
        ]
    out["source"] = src
    out["claims"] = claims
    if table == "exercise_variant_version":
        out["missing_dose_fields"] = []
        uses = conn.execute("select * from clinical_use_version where variant_version_id=%s", (vid,)).fetchall()
        for u in uses:
            for k, f in (u["dose_envelope"] or {}).items():
                if f.get("value") is None and not f.get("range"):
                    out["missing_dose_fields"].append(
                        {
                            "clinical_use_version_id": str(u["id"]),
                            "field": k,
                            "null_reason": f.get("null_reason"),
                        }
                    )
        out["clinical_uses"] = [_clean(u) for u in uses]
        out["media"] = [_clean(m) for m in conn.execute("select * from media_asset_version where variant_version_id=%s", (vid,)).fetchall()]
        out["duplicate_proposal"] = None
        if row["duplicate_of_entity_id"]:
            t = conn.execute(
                "select * from exercise_variant_version where entity_id=%s order by version desc limit 1",
                (row["duplicate_of_entity_id"],),
            ).fetchone()
            out["duplicate_proposal"] = _clean(t) if t else None
        out["conflicts"] = [_clean(c) for c in claims if c.get("conflicts_with_claim_id")] if claims else []
        out["ocr_warnings"] = [c for c in claims if c.get("ocr_confidence") is not None and c["ocr_confidence"] < 0.8] if claims else []
        out["diagnostic_mappings"] = [
            _clean(m)
            for m in conn.execute(
                "select m.*, tc.code, tc.descriptor from diagnosis_mapping_version m join terminology_code tc on tc.id=m.terminology_code_id "
                "where m.condition_id in (select condition_id from clinical_use_version where variant_version_id=%s)",
                (vid,),
            ).fetchall()
        ]
        out["versions"] = [
            _clean(v)
            for v in conn.execute(
                "select id, version, approval_state, created_at, approved_at, invalidated_at from exercise_variant_version where entity_id=%s order by version",
                (row["entity_id"],),
            ).fetchall()
        ]
    out["review_events"] = [
        _clean(e)
        for e in conn.execute(
            "select * from review_event where entity_table=%s and version_id=%s order by created_at",
            (table, vid),
        ).fetchall()
    ]
    return out


def _clean(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}
