"""M1 from the September 2026 review: the system understands "knee replacement" and "hip replacement".

Both packs are unsigned placeholders — structure only, every dose null, every threshold a rule slot. What these
tests pin is that the owner's sentence resolves to a condition, the planner asks for the post-operative facts a
rule can actually test, the derived `days_since_procedure` exists, and the hip's approach-specific exclusion rules
validate against the rule language.
"""

from __future__ import annotations

from datetime import UTC, datetime

from _helpers import K
from moveai_contracts.enums import FieldStatus
from moveai_contracts.intake import Intake, IntakeField
from moveai_ingestion.config import FIXTURES
from moveai_planner.engine import derive_time_fields
from moveai_rules import load_pack
from moveai_rules.ast import evaluate

TKA_SENTENCE = "total knee arthroplasty, right knee, 50-year-old healthy male"


def test_the_owners_sentence_resolves_to_the_tka_condition(run):
    r = run(TKA_SENTENCE, case_ref="tka-1")
    assert r.status == "needs_assessment"
    assert r.candidate_conditions == ["Total knee arthroplasty, primary"]
    assert r.pathway_previews, "an unsigned pack still yields a labelled preview pathway"
    missing = {m.field for m in r.missing_fields}
    # the post-op facts a rule can test, not just free text
    assert {
        "weight_bearing_status",
        "fixation",
        "knee_flexion_active_deg",
        "knee_extension_deficit_deg",
        "extension_lag_deg",
        "assistive_device",
        "wound_status",
    } <= missing
    assert "affected_side" in missing  # "right knee" is proposed, never established, from a narrative


def test_hip_replacement_resolves_and_asks_for_the_approach(run):
    r = run("left total hip replacement two weeks ago", case_ref="tha-1")
    assert r.candidate_conditions == ["Total hip arthroplasty, primary"]
    assert "surgical_approach" in {m.field for m in r.missing_fields}


def test_plain_words_work_too(run):
    assert run("knee replacement", case_ref="tka-2").candidate_conditions == ["Total knee arthroplasty, primary"]
    assert run("new hip", case_ref="tha-2").candidate_conditions == ["Total hip arthroplasty, primary"]


def test_knee_and_hip_replacement_do_not_collide(run):
    r = run("total knee replacement", case_ref="tka-3")
    assert r.candidate_conditions == ["Total knee arthroplasty, primary"]


def test_days_since_procedure_is_derived_never_entered():
    intake = Intake(
        fields={
            "procedure_date": K("2026-09-01"),
            "days_since_procedure": IntakeField.known(999, provenance="pt_entered"),  # a client claiming the value is discarded
        }
    )
    out = derive_time_fields(intake, datetime(2026, 9, 15, tzinfo=UTC))
    f = out.get("days_since_procedure")
    assert f.is_known and f.value == 14 and f.unit == "days" and f.provenance == "derived"


def test_days_since_procedure_stays_unknown_without_a_date():
    out = derive_time_fields(Intake(fields={"days_since_procedure": K(3)}), datetime.now(UTC))
    assert out.get("days_since_procedure").status == FieldStatus.unknown
    out = derive_time_fields(Intake(fields={"procedure_date": K("not a date")}), datetime.now(UTC))
    assert out.get("days_since_procedure").status == FieldStatus.unknown


def test_a_future_procedure_date_does_not_produce_a_negative_day_count():
    out = derive_time_fields(Intake(fields={"procedure_date": K("2030-01-01")}), datetime(2026, 9, 15, tzinfo=UTC))
    assert out.get("days_since_procedure").status == FieldStatus.unknown


def test_hip_approach_rules_validate_and_fire_on_the_new_field():
    pack = load_pack(FIXTURES / "content-packs" / "total_hip_arthroplasty.yaml")
    rules = {r.key: r for r in pack.rules}
    posterior = rules["posterior_precautions"]
    assert posterior.action.type == "exclude_variant" and "hip_flexion_over_90" in posterior.action.tags
    intake = Intake(fields={"surgical_approach": IntakeField.known("posterior")})
    assert evaluate(posterior.expression, intake).result.name == "TRUE"
    assert evaluate(rules["anterior_precautions"].expression, intake).result.name == "FALSE"
    # an unknown approach is unknown, not "no precautions"
    assert evaluate(posterior.expression, Intake()).result.name == "UNKNOWN"


def test_tka_rules_reference_only_real_intake_fields():
    """Loading validates every rule expression against INTAKE_FIELDS; a typo in a field name fails here."""
    pack = load_pack(FIXTURES / "content-packs" / "total_knee_arthroplasty.yaml")
    assert pack.approval_state == "unsigned_placeholder" and not pack.signed
    assert {r.key for r in pack.rules} >= {
        "req_side",
        "req_surgeon_status",
        "req_current_assessment",
        "protected_weight_bearing",
        "earliest_start_slot",
    }
    fields = set()
    for r in pack.rules:
        fields |= r.expression.fields()
    assert {"weight_bearing_status", "knee_flexion_active_deg", "days_since_procedure"} <= fields
