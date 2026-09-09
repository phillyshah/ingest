"""Draft editing, approval signing, retrieval, reassessment, and withdrawal propagation (spec §8 steps 9-10, §10, §13 #14/#18)."""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Any

import psycopg
from moveai_contracts.intake import CaseSubmission, Intake
from moveai_contracts.plan import DraftPatch, PlanOption
from moveai_db import J

from .catalog import PRESCRIBABLE, release_contains
from .engine import content_hash, narrative_for, narrative_matches, validate_option


class PlanError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


def _secret() -> bytes:
    s = os.environ.get("PLAN_SIGNING_SECRET")
    if not s:
        raise RuntimeError("PLAN_SIGNING_SECRET is not set")
    return s.encode()


def sign(case_snapshot_id: Any, plan_hash: str, catalog_release_id: Any, revision: int) -> str:
    msg = f"{case_snapshot_id}|{plan_hash}|{catalog_release_id}|{revision}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def latest_revision(conn: psycopg.Connection, tenant_id: Any, plan_id: Any) -> dict | None:
    return conn.execute(
        "select * from plan_version where tenant_id=%s and plan_id=%s order by revision desc limit 1",
        (tenant_id, plan_id),
    ).fetchone()


def _options(row: dict) -> list[PlanOption]:
    return [PlanOption.model_validate(o) for o in row["options"]]


def patch_draft(conn: psycopg.Connection, *, tenant_id: Any, user_id: Any, plan_id: Any, patch: DraftPatch) -> dict:
    cur = latest_revision(conn, tenant_id, plan_id)
    if not cur:
        raise PlanError("not_found", "plan not found", 404)
    if cur["revision"] != patch.expected_revision:
        raise PlanError("stale_revision", f"expected revision {patch.expected_revision}, current is {cur['revision']}")
    if cur["status"] == "approved":
        raise PlanError("immutable", "approved plans cannot be edited; create a reassessment")
    if cur["routing"] != "draft_ready":
        raise PlanError("not_draft_ready", f"plan routing is {cur['routing']}; nothing to edit")
    options = _options(cur)
    snap = conn.execute("select intake from case_snapshot where id=%s", (cur["case_snapshot_id"],)).fetchone()
    intake = Intake.model_validate({"fields": snap["intake"]})
    selected_id = patch.selected_option_id or cur["option_id"]
    if not selected_id:
        raise PlanError("no_option", "select an option first", 422)
    opt = next((o for o in options if o.option_id == selected_id), None)
    if not opt:
        raise PlanError("unknown_option", "option not in this plan", 422)
    if patch.items is not None:
        # PT edits: every item must be an approved/published variant present in the pinned release; full revalidation
        for it in patch.items:
            v = conn.execute("select approval_state from exercise_variant_version where id=%s", (it.variant_version_id,)).fetchone()
            if not v or v["approval_state"] not in PRESCRIBABLE:
                raise PlanError("unpublished_variant", f"variant {it.variant_version_id} is not approved/published", 422)
            if cur["catalog_release_id"] and not release_contains(conn, cur["catalog_release_id"], it.variant_version_id):
                raise PlanError(
                    "not_in_release",
                    f"variant {it.variant_version_id} is not in the pinned catalog release",
                    422,
                )
            for name, f in it.prescribed_dose.fields.items() if it.prescribed_dose else []:
                if f.is_set and f.provenance == "clinician_authored" and not f.author_id:
                    raise PlanError("dose_provenance", f"{name}: clinician-authored value needs author_id", 422)
        opt.items = patch.items
    if patch.rationale is not None:
        if not narrative_matches(opt, patch.rationale):
            raise PlanError("narrative_mismatch", "rationale numbers do not match the structured prescription", 422)
    opt.validation = validate_option(opt, intake)
    opt.content_hash = content_hash(opt.model_dump(mode="json", exclude={"content_hash", "option_id", "validation"}))
    new_options = [opt if o.option_id == opt.option_id else o for o in options]
    payload = [o.model_dump(mode="json") for o in new_options]
    row = conn.execute(
        """insert into plan_version(tenant_id, plan_id, revision, case_snapshot_id, catalog_release_id, status, routing, option_id, options,
             selected_items, validation, rationale, protocol_version_ids, rule_version_ids, content_hash, author_id, segment_explanations)
           values (%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
        (
            tenant_id,
            plan_id,
            cur["revision"] + 1,
            cur["case_snapshot_id"],
            cur["catalog_release_id"],
            cur["routing"],
            opt.option_id,
            J(payload),
            J([i.model_dump(mode="json") for i in opt.items]),
            J(opt.validation.model_dump()),
            patch.rationale or narrative_for(opt),
            [uuid.UUID(x) for x in opt.protocol_version_ids],
            [uuid.UUID(x) for x in opt.rule_version_ids],
            opt.content_hash,
            user_id,
            cur["segment_explanations"] if isinstance(cur["segment_explanations"], str) else J(cur["segment_explanations"]),
        ),
    ).fetchone()
    conn.execute("update plan_version set status='superseded' where id=%s", (cur["id"],))
    conn.execute(
        "insert into review_event(tenant_id, entity_table, version_id, reviewer_id, reviewer_role, decision, reason, changes) values (%s,'plan_version',%s,%s,'pt','edit',%s,%s)",
        (
            tenant_id,
            row["id"],
            user_id,
            patch.edit_reason,
            J({"selected_option_id": opt.option_id, "items_changed": patch.items is not None}),
        ),
    )
    return row


def _dependency_checks(conn: psycopg.Connection, row: dict, opt: PlanOption) -> list[str]:
    problems = []
    for it in opt.items:
        for table, vid in (
            ("exercise_variant_version", it.variant_version_id),
            ("clinical_use_version", it.clinical_use_version_id),
        ):
            v = conn.execute(f"select approval_state from {table} where id=%s", (vid,)).fetchone()
            if not v or v["approval_state"] not in PRESCRIBABLE:
                problems.append(f"{table} {vid} is {v['approval_state'] if v else 'missing'}")
        if it.media_version_id:
            m = conn.execute(
                "select m.media_state, g.can_display_to_patient, g.expires_at, g.revoked_at from media_asset_version m left join rights_grant g on g.id=m.rights_grant_id where m.id=%s",
                (it.media_version_id,),
            ).fetchone()
            if not m or m["can_display_to_patient"] != "allowed" or m["revoked_at"] or m["media_state"] != "graphic_available":
                problems.append(f"media {it.media_version_id} no longer displayable; text-only presentation required")
        for cid in it.evidence_claim_ids:
            sv = conn.execute(
                "select sv.pipeline_state, g.revoked_at, g.expires_at from evidence_claim c join source_version sv on sv.id=c.source_version_id "
                "left join rights_grant g on g.source_version_id=sv.id where c.id=%s",
                (cid,),
            ).fetchone()
            if sv and (sv["pipeline_state"] == "withdrawn" or sv["revoked_at"]):
                problems.append(f"evidence claim {cid} source withdrawn/revoked")
    for pid in opt.protocol_version_ids:
        p = conn.execute("select approval_state from protocol_version where id=%s", (pid,)).fetchone()
        if not p or p["approval_state"] not in PRESCRIBABLE:
            problems.append(f"protocol {pid} is {p['approval_state'] if p else 'missing'}")
    return problems


def approve(
    conn: psycopg.Connection,
    *,
    tenant_id: Any,
    user_id: Any,
    roles: list[str],
    plan_id: Any,
    expected_revision: int,
    attestation: str,
) -> dict:
    cur = latest_revision(conn, tenant_id, plan_id)
    if not cur:
        raise PlanError("not_found", "plan not found", 404)
    if cur["revision"] != expected_revision:
        raise PlanError("stale_revision", f"expected revision {expected_revision}, current is {cur['revision']}")
    if cur["status"] == "approved":
        raise PlanError("already_approved", "this revision is already approved")
    if cur["routing"] != "draft_ready" or not cur["option_id"]:
        raise PlanError("not_approvable", "select and validate a draft option first", 422)
    if not ({"pt", "clinical_lead"} & set(roles)):
        raise PlanError("forbidden", "only a PT or clinical lead may approve", 403)
    opt = next(o for o in _options(cur) if o.option_id == cur["option_id"])
    snap = conn.execute("select intake from case_snapshot where id=%s", (cur["case_snapshot_id"],)).fetchone()
    validation = validate_option(opt, Intake.model_validate({"fields": snap["intake"]}))
    if not validation.passed:
        raise PlanError("validation_failed", "; ".join(validation.blockers), 422)
    problems = _dependency_checks(conn, cur, opt)
    if problems:
        raise PlanError("dependency_changed", "; ".join(problems))
    recomputed = content_hash(opt.model_dump(mode="json", exclude={"content_hash", "option_id", "validation"}))
    if recomputed != cur["content_hash"]:
        raise PlanError("hash_mismatch", "plan content changed since it was drafted")
    sig = sign(cur["case_snapshot_id"], cur["content_hash"], cur["catalog_release_id"], cur["revision"])
    # transactional race check: lock the row and re-verify no newer revision appeared
    conn.execute("select 1 from plan_version where id=%s for update", (cur["id"],))
    newer = conn.execute(
        "select 1 from plan_version where tenant_id=%s and plan_id=%s and revision > %s",
        (tenant_id, plan_id, cur["revision"]),
    ).fetchone()
    if newer:
        raise PlanError("stale_revision", "a newer revision exists")
    row = conn.execute(
        "update plan_version set status='approved', approved_by=%s, approved_at=now(), approval_signature=%s where id=%s returning *",
        (user_id, sig, cur["id"]),
    ).fetchone()
    conn.execute(
        "insert into review_event(tenant_id, entity_table, version_id, reviewer_id, reviewer_role, decision, reason) values (%s,'plan_version',%s,%s,'pt','approve',%s)",
        (tenant_id, cur["id"], user_id, attestation),
    )
    conn.execute(
        "insert into outbox_event(event_type, tenant_id, object_table, object_id, object_version, payload) values ('plan.approved',%s,'plan_version',%s,%s,%s)",
        (
            tenant_id,
            cur["id"],
            cur["revision"],
            J({"plan_id": str(plan_id), "revision": cur["revision"], "content_hash": cur["content_hash"]}),
        ),
    )
    return row


def get_approved(conn: psycopg.Connection, *, tenant_id: Any, plan_id: Any) -> dict:
    # an approved revision stays the lawful assigned version even while under a review hold (spec §15): it is
    # returned with hold=true and assignable=false rather than silently rewritten or hidden
    row = conn.execute(
        "select * from plan_version where tenant_id=%s and plan_id=%s and status in ('approved','review_required') order by revision desc limit 1",
        (tenant_id, plan_id),
    ).fetchone()
    if not row:
        latest = latest_revision(conn, tenant_id, plan_id)
        return {
            "available": False,
            "reason": (f"latest revision {latest['revision']} is {latest['status']} ({latest['routing']})" if latest else "unknown plan"),
        }
    expected = sign(row["case_snapshot_id"], row["content_hash"], row["catalog_release_id"], row["revision"])
    if not hmac.compare_digest(expected, row["approval_signature"] or ""):
        return {"available": False, "reason": "approval signature invalid"}
    review = row["status"] == "review_required"
    return {
        "available": True,
        "assignable": not review,
        "plan_id": str(plan_id),
        "revision": row["revision"],
        "case_snapshot_id": str(row["case_snapshot_id"]),
        "catalog_release_id": str(row["catalog_release_id"]) if row["catalog_release_id"] else None,
        "content_hash": row["content_hash"],
        "approval_signature": row["approval_signature"],
        "approved_at": row["approved_at"],
        "items": row["selected_items"],
        "option": next(o for o in row["options"] if o["option_id"] == row["option_id"]),
        "rationale": row["rationale"],
        "hold": review,
        "hold_reason": row["review_required_reason"] if review else None,
    }


def reassess(conn: psycopg.Connection, *, tenant_id: Any, user_id: Any, plan_id: Any, submission: CaseSubmission):
    from .engine import plan_options

    cur = latest_revision(conn, tenant_id, plan_id)
    if not cur:
        raise PlanError("not_found", "plan not found", 404)
    resp = plan_options(conn, tenant_id=tenant_id, user_id=user_id, submission=submission)
    resp.notes.append(f"reassessment of plan {plan_id}: prior approved revision (if any) stays assigned until a new revision is approved")
    return resp


def propagate_withdrawal(conn: psycopg.Connection, *, entity_table: str, version_id: Any, reason: str) -> dict[str, Any]:
    """Withdrawn/expired upstream -> dependent versions flagged, affected approved plans routed to review (spec §4, §13 #14)."""
    affected_versions: set[tuple[str, str]] = set()
    frontier = [(entity_table, str(version_id))]
    while frontier:
        t, vid = frontier.pop()
        for e in conn.execute(
            "select downstream_table, downstream_id from dependency_edge where upstream_table=%s and upstream_id=%s",
            (t, vid),
        ).fetchall():
            key = (e["downstream_table"], str(e["downstream_id"]))
            if key not in affected_versions:
                affected_versions.add(key)
                frontier.append(key)
    for t, vid in affected_versions:
        if t in (
            "exercise_variant_version",
            "clinical_use_version",
            "protocol_version",
            "media_asset_version",
        ):
            conn.execute(
                f"update {t} set approval_state='withdrawn', withdrawn_at=now(), withdrawal_reason=%s where id=%s and approval_state in ('approved','published','pending_review','draft')",
                (f"upstream {entity_table} {version_id}: {reason}", vid),
            )
            conn.execute(
                "insert into catalog_change(change_type, entity_table, version_id) values ('withdrawn',%s,%s)",
                (t, vid),
            )
            conn.execute(
                "insert into outbox_event(event_type, object_table, object_id, payload) values ('content.withdrawn',%s,%s,%s)",
                (t, vid, J({"reason": reason})),
            )
    ids = [vid for t, vid in affected_versions if t in ("exercise_variant_version", "clinical_use_version", "protocol_version")] + [
        str(version_id)
    ]
    plans = conn.execute(
        """select id, tenant_id, plan_id, revision from plan_version where status='approved' and (
             protocol_version_ids && %s::uuid[] or exists (select 1 from jsonb_array_elements(selected_items) i
               where i->>'variant_version_id' = any(%s) or i->>'clinical_use_version_id' = any(%s)))""",
        ([uuid.UUID(x) for x in ids if _is_uuid(x)], ids, ids),
    ).fetchall()
    for p in plans:
        conn.execute(
            "update plan_version set status='review_required', review_required_reason=%s where id=%s",
            (f"{entity_table} {version_id} withdrawn: {reason}", p["id"]),
        )
        conn.execute(
            "insert into outbox_event(event_type, tenant_id, object_table, object_id, object_version, payload) values ('plan.review_required',%s,'plan_version',%s,%s,%s)",
            (
                p["tenant_id"],
                p["id"],
                p["revision"],
                J({"plan_id": str(p["plan_id"]), "reason": "dependency withdrawn"}),
            ),
        )
    return {
        "affected_versions": sorted(f"{t}:{v}" for t, v in affected_versions),
        "plans_routed_to_review": [str(p["plan_id"]) for p in plans],
    }


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
