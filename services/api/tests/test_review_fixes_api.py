"""API-side fixes from the September 2026 backend review: technique review, dose-claim verification, region."""

from __future__ import annotations

import uuid

from moveai_ingestion.pipeline import run_all

from .test_api import ALL_ALLOWED, _register_and_ingest


def _a_pending_variant_with_a_photo(admin, conn):
    """An ingested variant plus a stored photo row hanging off it (the shape the PDF-upload path produces)."""
    _, _, svid = _register_and_ingest(admin, conn, approve=True)
    v = conn.execute(
        "select v.id from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s limit 1",
        (svid,),
    ).fetchone()
    grant = conn.execute("select id from rights_grant where source_version_id=%s", (svid,)).fetchone()
    m = conn.execute(
        """insert into media_asset_version(entity_id, version, variant_version_id, media_type, media_state, storage_ref, rights_grant_id, approval_state)
           values (%s,1,%s,'still_graphic','graphic_available','local://photo.png',%s,'draft') returning id""",
        (uuid.uuid4(), v["id"], grant["id"]),
    ).fetchone()
    return svid, v["id"], m["id"]


def test_a_pt_can_record_that_a_photo_shows_the_exercise_correctly(admin, pt, conn, seeded):
    _, _, mid = _a_pending_variant_with_a_photo(admin, conn)
    before = conn.execute("select technique_review_passed from media_asset_version where id=%s", (mid,)).fetchone()
    assert before["technique_review_passed"] is None  # the flag nothing ever set
    r = pt.post(
        "/reviews",
        json={
            "entity_table": "media_asset_version",
            "version_id": str(mid),
            "decision": "technique_review",
            "reason": "clear demonstration",
        },
    )
    assert r.status_code == 201, r.text
    after = conn.execute("select technique_review_passed, technique_reviewer_id from media_asset_version where id=%s", (mid,)).fetchone()
    assert after["technique_review_passed"] is True and after["technique_reviewer_id"] == seeded["pt"]
    ev = conn.execute(
        "select decision, reviewer_role from review_event where entity_table='media_asset_version' and version_id=%s", (mid,)
    ).fetchone()
    assert ev["decision"] == "technique_review" and ev["reviewer_role"] == "pt"


def test_a_failed_technique_review_is_recorded_as_false_not_left_blank(admin, pt, conn):
    _, _, mid = _a_pending_variant_with_a_photo(admin, conn)
    r = pt.post(
        "/reviews",
        json={"entity_table": "media_asset_version", "version_id": str(mid), "decision": "technique_review", "changes": {"passed": False}},
    )
    assert r.status_code == 201
    assert (
        conn.execute("select technique_review_passed from media_asset_version where id=%s", (mid,)).fetchone()["technique_review_passed"]
        is False
    )


def test_technique_review_is_a_clinical_decision(admin, rights, conn):
    _, _, mid = _a_pending_variant_with_a_photo(admin, conn)
    r = rights.post("/reviews", json={"entity_table": "media_asset_version", "version_id": str(mid), "decision": "technique_review"})
    assert r.status_code == 403


def test_technique_review_only_targets_media(admin, pt, conn):
    _, _, svid = _register_and_ingest(admin, conn, approve=True)
    v = conn.execute(
        "select v.id from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s limit 1",
        (svid,),
    ).fetchone()
    r = pt.post("/reviews", json={"entity_table": "exercise_variant_version", "version_id": str(v["id"]), "decision": "technique_review"})
    assert r.status_code == 422


# ---------------------------------------------------------------- dose claim verification on review edits
def _clinical_use_for(conn, pt_id):
    """A clinical use on a seeded (pack) variant, so a review edit of its dose_envelope can be attempted."""
    return conn.execute(
        "select cu.id, cu.variant_version_id from clinical_use_version cu where cu.approval_state in ('unsigned_placeholder','draft','pending_review') limit 1"
    ).fetchone()


def test_a_dose_naming_a_claim_that_does_not_exist_is_refused(admin, pt, conn, seeded):
    cu = _clinical_use_for(conn, seeded["pt"])
    body = {
        "entity_table": "clinical_use_version",
        "version_id": str(cu["id"]),
        "decision": "edit",
        "changes": {
            "dose_envelope": {"sets": {"value": 3, "unit": "count", "provenance": "source_explicit", "claim_id": str(uuid.uuid4())}}
        },
    }
    r = pt.post("/reviews", json=body)
    assert r.status_code == 422 and r.json()["code"] == "unknown_claim"


def test_a_dose_naming_an_instruction_claim_is_refused(admin, pt, conn, seeded):
    _, _, svid = _register_and_ingest(admin, conn, approve=True)
    instr = conn.execute(
        "select id from evidence_claim where source_version_id=%s and claim_type='exercise_instruction' limit 1", (svid,)
    ).fetchone()
    cu = _clinical_use_for(conn, seeded["pt"])
    body = {
        "entity_table": "clinical_use_version",
        "version_id": str(cu["id"]),
        "decision": "edit",
        "changes": {
            "dose_envelope": {"sets": {"value": 3, "unit": "count", "provenance": "source_explicit", "claim_id": str(instr["id"])}}
        },
    }
    r = pt.post("/reviews", json=body)
    assert r.status_code == 422 and r.json()["code"] == "not_a_dose_claim"


def test_a_clinician_authored_dose_must_name_the_person_entering_it(admin, pt, conn, seeded):
    cu = _clinical_use_for(conn, seeded["pt"])
    body = {
        "entity_table": "clinical_use_version",
        "version_id": str(cu["id"]),
        "decision": "edit",
        "changes": {
            "dose_envelope": {"sets": {"value": 3, "unit": "count", "provenance": "clinician_authored", "author_id": str(seeded["lead"])}}
        },
    }
    r = pt.post("/reviews", json=body)
    assert r.status_code == 422 and r.json()["code"] == "author_mismatch"
    ok = {
        **body,
        "changes": {
            "dose_envelope": {"sets": {"value": 3, "unit": "count", "provenance": "clinician_authored", "author_id": str(seeded["pt"])}}
        },
    }
    assert pt.post("/reviews", json=ok).status_code == 201


# ---------------------------------------------------------------- author ≠ approver at the database
def test_the_database_refuses_an_author_approving_their_own_variant(conn, seeded):
    import psycopg
    import pytest

    row = conn.execute("select id, created_by from exercise_variant_version where created_by is not null limit 1").fetchone()
    with pytest.raises(psycopg.errors.CheckViolation, match="author_not_approver"), conn.transaction():
        conn.execute("update exercise_variant_version set approved_by=%s where id=%s", (row["created_by"], row["id"]))


# ---------------------------------------------------------------- campaign region
def test_campaign_jobs_carry_the_conditions_body_region(admin, conn):
    """Was hard-coded 'unknown', which made every campaign-sourced exercise unfindable by region."""
    from .test_api import FIX

    scope = {
        "ailment_text": "surgically repaired MCL tear",
        "codes": [],
        "limits": {"max_sources": 2, "max_usd": 1.0, "max_search_requests": 10},
        "supplied_source_urls": [f"file://{FIX / 'owned_demo_protocol.html'}?r={uuid.uuid4().hex[:6]}"],
        "source_policy": "allowlist_and_supplied",
        "scope_confirmed": True,
    }
    c = admin.post("/ingestion-campaigns", json={"title": "region", "scope": scope}).json()
    admin.post(f"/ingestion-campaigns/{c['id']}/runs")
    regions = {
        r["payload"]["region"] for r in conn.execute("select payload from ingestion_job where campaign_id=%s", (c["id"],)).fetchall()
    }
    expected = conn.execute("select body_region from condition where internal_code='mcl_tear_surgical_repair'").fetchone()["body_region"]
    assert regions == {expected}


__all__ = ["ALL_ALLOWED", "run_all"]
