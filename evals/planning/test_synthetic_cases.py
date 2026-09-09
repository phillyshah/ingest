"""Run every synthetic case through the planner and compare with the (clinical-lead-pending) expected routing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from moveai_contracts.intake import CaseSubmission, Intake
from moveai_planner.engine import plan_options
from moveai_planner.seed import seed

CASES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic-cases"
CASES = [c for f in sorted(CASES_DIR.glob("*.json")) for c in json.loads(f.read_text())]


@pytest.fixture(scope="module")
def seeded_conn(migrated_db):
    from moveai_db import connect

    c = connect(migrated_db)
    s = seed(c)
    yield c, s
    c.rollback()
    c.close()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(seeded_conn, case):
    conn, s = seeded_conn
    sub = CaseSubmission(case_ref=case["id"], narrative=case["narrative"], intake=Intake.model_validate(case["intake"]))
    r = plan_options(conn, tenant_id=s["tenant_id"], user_id=s["pt"], submission=sub)
    exp = case["expected"]
    assert r.status.value == exp["status"], f"{case['id']}: got {r.status.value}; missing={[m.field for m in r.missing_fields]}; blocked={r.blocked}"
    missing = {m.field for m in r.missing_fields}
    for f in exp["missing_fields_include"]:
        assert f in missing, f"{case['id']}: expected missing field {f}; got {sorted(missing)}"
    if exp.get("blocked_rule_contains"):
        assert r.blocked and (exp["blocked_rule_contains"] in r.blocked.rule_name or exp["blocked_rule_contains"] in r.blocked.message)
    if r.status.value == "needs_assessment":
        assert r.prescription is None and r.assignable is False
    if r.status.value == "draft_ready":
        assert r.options and all(i.media_version_id is None for o in r.options for i in o.items)


def test_case_count_meets_spec():
    by = {}
    for c in CASES:
        by[c["family"]] = by.get(c["family"], 0) + 1
    assert all(by[f] >= 10 for f in ("frozen_shoulder", "mcl_repair", "hamstring_strain")), by
    assert len(CASES) >= 30


def test_paired_bmi_cases_identical_eligibility(seeded_conn):
    conn, s = seeded_conn
    a, b = (next(c for c in CASES if c["id"] == i) for i in ("demo-05-paired-bmi-a", "demo-06-paired-bmi-b"))
    ra, rb = (plan_options(conn, tenant_id=s["tenant_id"], user_id=s["pt"], submission=CaseSubmission(case_ref=c["id"], narrative=c["narrative"], intake=Intake.model_validate(c["intake"]))) for c in (a, b))
    assert [i.variant_version_id for i in ra.options[0].items] == [i.variant_version_id for i in rb.options[0].items]
    assert ra.options[0].items[0].prescribed_dose == rb.options[0].items[0].prescribed_dose
