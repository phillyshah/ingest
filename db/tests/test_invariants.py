"""Database-enforced invariants (spec §6, §11, §13 #8/#15). These must hold regardless of application code."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from moveai_db import J, tenant_context


def _concept(conn):
    return conn.execute(
        "insert into exercise_concept(preferred_name, body_region) values (%s,'shoulder') returning id",
        (f"concept-{uuid.uuid4().hex[:8]}",),
    ).fetchone()["id"]


def _variant(conn, concept_id, state="draft", **over):
    row = dict(
        concept_id=concept_id,
        entity_id=uuid.uuid4(),
        version=1,
        name="Assisted ER",
        region="shoulder",
        assistance="active_assisted",
        approval_state=state,
    )
    row.update(over)
    cols = ",".join(row)
    ph = ",".join(["%s"] * len(row))
    return conn.execute(f"insert into exercise_variant_version({cols}) values ({ph}) returning id", list(row.values())).fetchone()["id"]


def test_approved_variant_is_immutable(conn):
    vid = _variant(conn, _concept(conn), state="approved")
    with pytest.raises(psycopg.errors.RaiseException, match="immutable_version"):
        with conn.transaction():
            conn.execute("update exercise_variant_version set name='edited' where id=%s", (vid,))
    # allowed terminal transitions
    conn.execute(
        "update exercise_variant_version set approval_state='withdrawn', withdrawn_at=now(), withdrawal_reason='test' where id=%s",
        (vid,),
    )
    assert (
        conn.execute("select approval_state from exercise_variant_version where id=%s", (vid,)).fetchone()["approval_state"] == "withdrawn"
    )


def test_approved_cannot_go_back_to_draft(conn):
    vid = _variant(conn, _concept(conn), state="published")
    with pytest.raises(psycopg.errors.RaiseException, match="immutable_version"):
        with conn.transaction():
            conn.execute("update exercise_variant_version set approval_state='draft' where id=%s", (vid,))


def test_draft_variant_is_editable(conn):
    vid = _variant(conn, _concept(conn))
    conn.execute("update exercise_variant_version set name='edited' where id=%s", (vid,))
    assert conn.execute("select name from exercise_variant_version where id=%s", (vid,)).fetchone()["name"] == "edited"


def test_audit_event_is_append_only(conn):
    aid = conn.execute("insert into audit_event(actor_kind, action) values ('service','test') returning id").fetchone()["id"]
    with pytest.raises(psycopg.errors.RaiseException, match="append_only"):
        with conn.transaction():
            conn.execute("update audit_event set action='x' where id=%s", (aid,))
    with pytest.raises(psycopg.errors.RaiseException, match="append_only"):
        with conn.transaction():
            conn.execute("delete from audit_event where id=%s", (aid,))


def test_cross_tenant_plan_reference_rejected(conn, tenants):
    a, b = tenants["tenant_a"], tenants["tenant_b"]
    with tenant_context(conn, a):
        snap = conn.execute("insert into case_snapshot(tenant_id, case_ref) values (%s,'c1') returning id", (a,)).fetchone()["id"]
    with tenant_context(conn, b):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    "insert into plan_version(tenant_id, plan_id, case_snapshot_id, routing, content_hash) values (%s,%s,%s,'needs_assessment','h')",
                    (b, uuid.uuid4(), snap),
                )


def test_rls_hides_other_tenant_rows(conn, tenants):
    a, b = tenants["tenant_a"], tenants["tenant_b"]
    with tenant_context(conn, a):
        conn.execute("insert into case_snapshot(tenant_id, case_ref) values (%s,'c1')", (a,))
    with tenant_context(conn, b):
        conn.execute("insert into case_snapshot(tenant_id, case_ref) values (%s,'c2')", (b,))
    # switch to the RLS-bound application role for the check
    conn.execute("set local role moveai_app")
    with tenant_context(conn, a):
        refs = [r["case_ref"] for r in conn.execute("select case_ref from case_snapshot").fetchall()]
        assert refs == ["c1"]
    with tenant_context(conn, None):
        assert conn.execute("select count(*) as n from case_snapshot").fetchone()["n"] == 0
    with tenant_context(conn, a):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("insert into case_snapshot(tenant_id, case_ref) values (%s,'sneaky')", (b,))
    conn.execute("reset role")


def test_dose_shape_constraint(conn):
    concept = _concept(conn)
    vid = _variant(conn, concept)
    cond = conn.execute(
        "insert into condition(internal_code, preferred_name, body_region) values (%s,'x','shoulder') returning id",
        (f"c-{uuid.uuid4().hex[:6]}",),
    ).fetchone()["id"]
    good = {
        "sets": {"value": 3, "provenance": "clinician_authored"},
        "repetitions": {"value": None, "null_reason": "not in source", "provenance": "unknown"},
    }
    conn.execute(
        "insert into clinical_use_version(entity_id, version, variant_version_id, condition_id, dose_envelope) values (%s,1,%s,%s,%s)",
        (uuid.uuid4(), vid, cond, J(good)),
    )
    bad = {"sets": {"value": 3}}  # no provenance
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "insert into clinical_use_version(entity_id, version, variant_version_id, condition_id, dose_envelope) values (%s,1,%s,%s,%s)",
                (uuid.uuid4(), vid, cond, J(bad)),
            )


def test_author_cannot_approve_own_rule(conn, users):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "insert into rule_version(entity_id, version, name, rule_kind, expression, action, author_id, approver_id) "
                "values (%s,1,'r','eligibility','{}','{}',%s,%s)",
                (uuid.uuid4(), users["pt"], users["pt"]),
            )


def test_applicability_action_requires_executable_rule(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute(
                "insert into population_applicability_version(target_version_id, target_type, relationship_type, action_type) "
                "values (%s,'clinical_use','precaution','exclude')",
                (uuid.uuid4(),),
            )


def test_budget_reservation_is_atomic_and_bounded(conn, tenants, users):
    camp = conn.execute(
        "insert into ingestion_campaign(tenant_id, title) values (%s,'t') returning id",
        (tenants["tenant_a"],),
    ).fetchone()["id"]
    scope = conn.execute(
        "insert into campaign_scope_version(campaign_id, version, desired_output, limits) values (%s,1,'x',%s) returning id",
        (
            camp,
            J(
                {
                    "max_usd": 1.0,
                    "max_search_requests": 2,
                    "max_document_bytes": 1000,
                    "max_sources": 5,
                    "max_candidates": 5,
                    "max_runtime_seconds": 60,
                }
            ),
        ),
    ).fetchone()["id"]
    run = conn.execute(
        "insert into campaign_run(campaign_id, scope_version_id, run_number) values (%s,%s,1) returning id",
        (camp, scope),
    ).fetchone()["id"]
    ok = [conn.execute("select reserve_budget(%s,null,0.6,1,100) as ok", (run,)).fetchone()["ok"] for _ in range(3)]
    assert ok == [True, False, False]  # second would exceed 1.0 USD
    conn.execute("select settle_budget(%s,null,0.6,0.5)", (run,))
    b = conn.execute("select budget from campaign_run where id=%s", (run,)).fetchone()["budget"]
    assert float(b["spent_usd"]) == 0.5 and float(b["reserved_usd"]) == 0
