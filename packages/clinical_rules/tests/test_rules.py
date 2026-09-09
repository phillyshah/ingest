import pytest
from moveai_contracts.intake import Intake, IntakeField
from moveai_rules import Tri, classify_bmi, compute_bmi, evaluate, parse_expr, to_kg, to_meters
from moveai_rules.ast import Action


def intake(**kw):
    return Intake(fields={k: (v if isinstance(v, IntakeField) else IntakeField.known(v)) for k, v in kw.items()})


def test_unknown_field_is_unknown_not_false():
    e = parse_expr({"op": "eq", "field": "affected_side", "value": "right"})
    r = evaluate(e, Intake())
    assert r.result is Tri.UNKNOWN and r.unknown_fields == ["affected_side"]


def test_kleene_logic():
    unk = {"op": "eq", "field": "affected_side", "value": "right"}
    assert evaluate(parse_expr({"op": "or", "args": [unk, {"op": "true"}]}), Intake()).result is Tri.TRUE
    assert evaluate(parse_expr({"op": "and", "args": [unk, {"op": "false"}]}), Intake()).result is Tri.FALSE
    assert evaluate(parse_expr({"op": "not", "arg": unk}), Intake()).result is Tri.UNKNOWN


def test_absence_of_report_is_not_negative():
    # "no concerning findings" can only be TRUE when the finding was assessed and is empty
    e = parse_expr(
        {
            "op": "and",
            "args": [
                {"op": "known", "field": "concerning_findings"},
                {"op": "eq", "field": "concerning_findings", "value": []},
            ],
        }
    )
    assert evaluate(e, Intake()).result is Tri.FALSE
    assert evaluate(e, intake(concerning_findings=[])).result is Tri.TRUE
    assert evaluate(e, intake(concerning_findings=["night pain unrelated to movement"])).result is Tri.FALSE


def test_between_boundaries_and_units():
    e = parse_expr({"op": "between", "field": "age", "min": 20, "max": 65, "max_inclusive": False, "unit": "years"})
    assert evaluate(e, intake(age=IntakeField.known(65, unit="years"))).result is Tri.FALSE
    assert evaluate(e, intake(age=IntakeField.known(20, unit="years"))).result is Tri.TRUE
    assert evaluate(e, intake(age=IntakeField.known(30, unit="months"))).result is Tri.UNKNOWN


def test_unsupported_constructs_rejected():
    with pytest.raises(ValueError):
        parse_expr({"op": "regex", "field": "diagnosis", "value": ".*"})
    with pytest.raises(ValueError):
        parse_expr({"op": "eq", "field": "not_a_field", "value": 1})
    with pytest.raises(ValueError):
        parse_expr({"op": "eq", "field": "age", "value": 1, "sql": "drop table"})
    with pytest.raises(ValueError):
        Action(type="block_for_review")


def test_bmi_boundaries_unrounded():
    cases = {
        18.4999: "underweight",
        18.5: "healthy_weight",
        24.9999: "healthy_weight",
        25.0: "overweight",
        29.9999: "overweight",
        30.0: "class_1_obesity",
        35.0: "class_2_obesity",
        39.9999: "class_2_obesity",
        40.0: "class_3_obesity",
    }
    for v, k in cases.items():
        assert classify_bmi(v, 55).klass == k, v


def test_bmi_minor_and_unknown_age_not_classified():
    assert classify_bmi(31.0, 17).klass is None
    assert classify_bmi(31.0, None).klass is None
    assert classify_bmi(None, 40).klass is None


def test_unit_conversion():
    h = to_meters(70, "in")
    w = to_kg(200, "lb")
    assert abs(compute_bmi(h, w) - 28.69) < 0.01
    with pytest.raises(ValueError):
        to_kg(1, "stone")


def test_not_applicable_is_definite_false_not_unknown():
    e = parse_expr({"op": "eq", "field": "prior_session_response", "value": "worse"})
    r = evaluate(e, intake(prior_session_response=IntakeField(status="not_applicable")))
    assert r.result is Tri.FALSE and r.unknown_fields == []
    r = evaluate(e, intake(prior_session_response=IntakeField(status="not_assessed")))
    assert r.result is Tri.UNKNOWN
