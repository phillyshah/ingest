from __future__ import annotations

import pytest
from moveai_contracts.enums import FieldStatus
from moveai_contracts.intake import CaseSubmission, Intake, IntakeField
from moveai_planner.engine import plan_options
from moveai_planner.seed import seed


@pytest.fixture()
def seeded(conn):
    return seed(conn)


def K(value, **kw):
    return IntakeField(status=FieldStatus.known, value=value, provenance="pt_entered", **kw)


def NA():
    return IntakeField(status=FieldStatus.not_assessed)


@pytest.fixture()
def run(conn, seeded):
    def _run(narrative=None, fields=None, case_ref=None, **kw):
        sub = CaseSubmission(
            case_ref=case_ref or f"case-{abs(hash(narrative or '')) % 10**6}",
            narrative=narrative,
            intake=Intake(fields=fields or {}),
            **kw,
        )
        return plan_options(conn, tenant_id=seeded["tenant_id"], user_id=seeded["pt"], submission=sub)

    return _run


# Complete frozen-shoulder assessment (values are test inputs, not clinical claims)
FS_COMPLETE = {
    "affected_side": K("right"),
    "diagnosis_confirmation": K({"confirmed": True, "assessor": "pt"}),
    "irritability": K("low"),
    "rom_active": K({"flexion": 100, "er": 20}, unit="deg"),
    "rom_passive": K({"flexion": 110, "er": 25}, unit="deg"),
    "surgeon_restrictions": K("none"),
    "procedure": K("none"),
    "goals": K(["reach behind head"]),
    "prior_interventions": K([]),
    "concerning_findings": K([]),
    "age": K(55, unit="years"),
    "sex": K("male"),
    "functional_criteria_met": K(False),
    "prior_session_response": IntakeField(status=FieldStatus.not_applicable),
}

DEMO_COMPLETE = {
    "affected_side": K("left"),
    "concerning_findings": K([]),
    "surgeon_restrictions": K("none"),
    "equipment": K(["elastic band"]),
    "functional_criteria_met": K(False),
    "age": K(55, unit="years"),
    "prior_session_response": IntakeField(status=FieldStatus.not_applicable),
}
