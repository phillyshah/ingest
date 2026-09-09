"""Planner acceptance (spec §9, §13 tests 1, 2, 8, 10, 11, 12, 14, 16, 17, 18; §19 gate; §20B/D)."""

from __future__ import annotations

import pytest
from _helpers import DEMO_COMPLETE, FS_COMPLETE, NA, K
from moveai_contracts.intake import CaseSubmission, Intake
from moveai_contracts.plan import DraftPatch
from moveai_planner.approval import PlanError, approve, get_approved, patch_draft, propagate_withdrawal
from moveai_planner.engine import narrative_for, narrative_matches

CANONICAL = {
    "frozen_shoulder": "55-year-old slightly obese man with frozen shoulder",
    "mcl_repair": "Patient recovering after surgical MCL repair",
    "hamstring_strain": "Patient with a hamstring pull, treated with physical therapy and no surgery",
}


@pytest.mark.parametrize("family,text", list(CANONICAL.items()))
def test_bare_input_returns_previews_and_missing_fields_not_a_prescription(run, family, text):
    r = run(text)
    assert r.status == "needs_assessment"
    assert r.prescription is None and r.assignable is False and r.requires_pt_review
    assert r.candidate_conditions, family
    assert r.pathway_previews and all(p.label.startswith("Protocol preview") for p in r.pathway_previews)
    assert all(row.time_window.provisional for p in r.pathway_previews for row in p.timeline)
    assert any(m.field == "affected_side" for m in r.missing_fields)
    assert any(m.field == "concerning_findings" for m in r.missing_fields)
    # every exercise in every preview carries a source reference (spec §20D)
    for p in r.pathway_previews:
        for row in p.timeline:
            for ex in row.exercises:
                assert ex.source_references, ex.variant_name
                assert ex.media_version_id is None  # text-only
    assert r.timeline_anchor and r.phase_windows


def test_frozen_shoulder_preserves_reported_body_size_and_does_not_infer_bmi(run):
    r = run(CANONICAL["frozen_shoulder"])
    bmi = next(s for s in r.segment_explanations if s.dimension == "bmi")
    assert "slightly obese" in (bmi.observed or "") and bmi.classification is None
    age = next(s for s in r.segment_explanations if s.dimension == "age")
    assert age.observed == "55 years"
    assert "recorded for context" in age.effect or "not an exclusion" in age.effect or "could not be evaluated" in age.effect
    assert any("no BMI class inferred" in a for a in r.assumptions)


def test_unknown_laterality_never_defaults(run):
    r = run(
        "frozen shoulder, right side mentioned in passing",
        fields={k: v for k, v in FS_COMPLETE.items() if k != "affected_side"},
    )
    assert r.status == "needs_assessment"
    assert any(m.field == "affected_side" for m in r.missing_fields)


def test_mcl_missing_surgeon_restrictions_blocks_individual_loading(run):
    fields = {
        "affected_side": K("left"),
        "procedure": K("MCL repair"),
        "procedure_date": K("2026-08-01"),
        "procedure_type": K("repair"),
        "associated_procedures": K([]),
        "rom_active": K({"flexion": 60}, unit="deg"),
        "pain_movement": K(3),
        "concerning_findings": K([]),
    }
    r = run(CANONICAL["mcl_repair"], fields=fields)
    assert r.status == "needs_assessment"
    missing = {m.field for m in r.missing_fields}
    assert {
        "surgeon_restrictions",
        "brace_restrictions",
        "weight_bearing_restrictions",
        "rom_restrictions",
    } <= missing
    assert r.pathway_previews and all(row.unknown_restrictions for p in r.pathway_previews for row in p.timeline)


def test_mcl_reconstruction_is_blocked_not_interchanged(run):
    fields = {
        "affected_side": K("left"),
        "procedure": K("MCL reconstruction"),
        "procedure_date": K("2026-08-01"),
        "procedure_type": K("reconstruction"),
        "associated_procedures": K([]),
        "surgeon_restrictions": K("brace locked"),
        "brace_restrictions": K("locked 0-30"),
        "weight_bearing_restrictions": K("TDWB"),
        "rom_restrictions": K("0-30"),
        "rom_active": K({"flexion": 30}, unit="deg"),
        "pain_movement": K(2),
        "concerning_findings": K([]),
    }
    r = run(CANONICAL["mcl_repair"], fields=fields)
    assert r.status == "blocked_for_clinical_review" and "Reconstruction" in r.blocked.message


def test_hamstring_pull_is_candidate_only_until_confirmed(run):
    r = run(CANONICAL["hamstring_strain"])
    assert "Hamstring strain" in r.candidate_conditions[0]
    assert any(m.field == "severity" for m in r.missing_fields) and any(m.field == "diagnosis_confirmation" for m in r.missing_fields)


def test_concerning_findings_block(run):
    r = run(
        CANONICAL["frozen_shoulder"],
        fields={**FS_COMPLETE, "concerning_findings": K(["progressive weakness"])},
    )
    assert r.status == "blocked_for_clinical_review" and r.blocked.rule_name.startswith("Concerning")


def test_worsening_response_routes_to_review(run):
    r = run(CANONICAL["frozen_shoulder"], fields={**FS_COMPLETE, "prior_session_response": K("worse")})
    assert r.status == "blocked_for_clinical_review" and "Worsen" in r.blocked.rule_name


def test_postoperative_frozen_shoulder_needs_own_protocol(run):
    r = run(CANONICAL["frozen_shoulder"], fields={**FS_COMPLETE, "procedure": K("capsular release")})
    assert r.status == "blocked_for_clinical_review" and "own approved protocol" in r.blocked.message


def test_complete_assessment_on_unsigned_pack_cannot_produce_a_draft(run):
    r = run(CANONICAL["frozen_shoulder"], fields=FS_COMPLETE)
    assert r.status == "blocked_for_clinical_review" and r.blocked.rule_name == "no_approved_pathway"
    assert r.options == [] and r.pathway_previews  # previews still visible for orientation
    assert all("unsigned_placeholder" in e.reason for e in r.excluded_candidates if e.protocol_version_id)


def test_missing_report_is_not_negative(run):
    r = run(CANONICAL["frozen_shoulder"], fields={**FS_COMPLETE, "concerning_findings": NA()})
    assert r.status == "needs_assessment" and any(m.field == "concerning_findings" for m in r.missing_fields)


def test_demo_pack_draft_ready_then_approve_then_retrieve(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="demo-1")
    assert r.status == "draft_ready" and len(r.options) == 1
    opt = r.options[0]
    assert opt.validation.passed, opt.validation
    assert [i.dose_label for i in opt.items] == ["prescribed"]
    assert opt.items[0].media_version_id is None  # text-only, null media valid
    assert opt.timeline[0].exercises[0].dose_label == "prescribed" and opt.timeline[1].exercises[0].dose_label == "protocol_example"
    # narrative equivalence: a mismatching rationale is rejected
    with pytest.raises(PlanError, match="narrative_mismatch"):
        patch_draft(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, rationale="do 5 sets of 50 reps"),
        )
    ok = narrative_for(opt)
    assert narrative_matches(opt, ok)
    rev2 = patch_draft(
        conn,
        tenant_id=seeded["tenant_id"],
        user_id=seeded["pt"],
        plan_id=r.plan_id,
        patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, rationale=ok),
    )
    assert rev2["revision"] == 2
    # stale approval request is rejected
    with pytest.raises(PlanError, match="stale_revision"):
        approve(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            roles=["pt"],
            plan_id=r.plan_id,
            expected_revision=1,
            attestation="x",
        )
    with pytest.raises(PlanError, match="forbidden"):
        approve(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["admin"],
            roles=["source_admin"],
            plan_id=r.plan_id,
            expected_revision=2,
            attestation="x",
        )
    approved = approve(
        conn,
        tenant_id=seeded["tenant_id"],
        user_id=seeded["pt"],
        roles=["pt"],
        plan_id=r.plan_id,
        expected_revision=2,
        attestation="reviewed",
    )
    assert approved["status"] == "approved" and approved["approval_signature"]
    got = get_approved(conn, tenant_id=seeded["tenant_id"], plan_id=r.plan_id)
    assert got["available"] and got["revision"] == 2 and got["content_hash"] == rev2["content_hash"]
    # every prescribed number resolves to provenance (spec §13 test 12)
    for it in got["items"]:
        for k, f in it["prescribed_dose"]["fields"].items():
            if f["value"] is not None:
                assert f["provenance"] in ("source_explicit", "clinician_authored") and (f["claim_id"] or f["author_id"]), k
    # approved plan is immutable; a further edit fails
    with pytest.raises(PlanError, match="immutable"):
        patch_draft(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=2, rationale=ok),
        )
    assert conn.execute("select count(*) as n from outbox_event where event_type='plan.approved'").fetchone()["n"] == 1


def test_demo_equipment_rule_excludes_resisted_variant_only_when_approved(run):
    # phase 2 holds only the resisted band variant; the approved rule excludes it when no band is available,
    # so no valid item remains and the engine says so instead of substituting silently
    r = run(
        "demo condition",
        fields={**DEMO_COMPLETE, "equipment": K([]), "functional_criteria_met": K(True)},
        case_ref="demo-eq",
    )
    assert r.status == "blocked_for_clinical_review" and r.blocked.rule_name == "no_valid_items"
    assert any("excluded by approved rule" in e.reason for e in r.excluded_candidates)
    # with the band, the same case yields a draft with the resisted variant
    r2 = run("demo condition", fields={**DEMO_COMPLETE, "functional_criteria_met": K(True)}, case_ref="demo-eq2")
    assert r2.status == "draft_ready" and r2.options[0].items[0].variant_name.startswith("Demo movement B")


def test_elapsed_time_cannot_advance_phase(run):
    r = run(
        "demo condition",
        fields={**DEMO_COMPLETE, "functional_criteria_met": K(False), "onset_date": K("2025-01-01")},
        case_ref="demo-time",
    )
    opt = r.options[0]
    assert opt.items[0].clinical_use_version_id == opt.timeline[0].exercises[0].clinical_use_version_id  # phase 1, not phase 2
    r2 = run("demo condition", fields={**DEMO_COMPLETE, "functional_criteria_met": K(True)}, case_ref="demo-time2")
    assert r2.options[0].items[0].variant_name.startswith("Demo movement B")


def test_paired_case_differing_only_in_bmi_gets_same_eligibility(run):
    a = run(
        "demo condition",
        fields={**DEMO_COMPLETE, "height": K(1.7, unit="m"), "weight": K(95, unit="kg")},
        case_ref="pair-a",
    )
    b = run(
        "demo condition",
        fields={**DEMO_COMPLETE, "height": K(1.7, unit="m"), "weight": K(65, unit="kg")},
        case_ref="pair-b",
    )
    assert a.status == b.status == "draft_ready"
    assert [i.variant_version_id for i in a.options[0].items] == [i.variant_version_id for i in b.options[0].items]
    assert a.options[0].items[0].prescribed_dose == b.options[0].items[0].prescribed_dose
    sa = next(s for s in a.segment_explanations if s.dimension == "bmi")
    sb = next(s for s in b.segment_explanations if s.dimension == "bmi")
    assert sa.classification == "class_1_obesity" and sb.classification == "healthy_weight"
    assert "no supported modification applied" in sa.effect or "not an exclusion" in sa.effect
    assert sa.rule_version_id is None


def test_minor_not_classified_under_adult_bmi(run):
    r = run(
        "demo condition",
        fields={
            **DEMO_COMPLETE,
            "age": K(16, unit="years"),
            "height": K(1.7, unit="m"),
            "weight": K(95, unit="kg"),
        },
        case_ref="minor",
    )
    s = next(s for s in r.segment_explanations if s.dimension == "bmi")
    assert s.classification and s.classification.startswith("not_classified")


def test_withdrawal_propagates_to_dependents_and_affected_plans(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="wd-1")
    opt = r.options[0]
    patch_draft(
        conn,
        tenant_id=seeded["tenant_id"],
        user_id=seeded["pt"],
        plan_id=r.plan_id,
        patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id),
    )
    approve(
        conn,
        tenant_id=seeded["tenant_id"],
        user_id=seeded["pt"],
        roles=["pt"],
        plan_id=r.plan_id,
        expected_revision=2,
        attestation="ok",
    )
    claim_id = opt.items[0].evidence_claim_ids[0]
    out = propagate_withdrawal(conn, entity_table="evidence_claim", version_id=claim_id, reason="source corrected")
    assert any(v.startswith("clinical_use_version:") for v in out["affected_versions"])
    assert r.plan_id in out["plans_routed_to_review"]
    got = get_approved(conn, tenant_id=seeded["tenant_id"], plan_id=r.plan_id)
    assert got["available"] and got["hold"] and got["assignable"] is False  # still retrievable, explicit hold, not assignable
    assert conn.execute("select count(*) as n from outbox_event where event_type='plan.review_required'").fetchone()["n"] == 1
    # a new draft on the withdrawn catalog can no longer use the withdrawn use
    r2 = run("demo condition", fields=DEMO_COMPLETE, case_ref="wd-2")
    assert r2.status != "draft_ready" or all(i.clinical_use_version_id != opt.items[0].clinical_use_version_id for i in r2.options[0].items)


def test_unpublished_variant_cannot_be_patched_into_a_plan(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="unpub")
    opt = r.options[0]
    draft = conn.execute(
        "insert into exercise_variant_version(concept_id, entity_id, version, name, region, assistance) "
        "select concept_id, gen_random_uuid(), 1, 'Draft thing', region, assistance from exercise_variant_version limit 1 returning id"
    ).fetchone()
    item = opt.items[0].model_copy(update={"variant_version_id": str(draft["id"])})
    with pytest.raises(PlanError, match="unpublished_variant"):
        patch_draft(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, items=[item]),
        )


def test_unrecognized_condition_says_so(run):
    r = run("left elbow thing")
    assert r.status == "needs_assessment" and r.pathway_previews == [] and any("fabricated" in n for n in r.notes)


def test_cross_tenant_plan_not_visible(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="xt")
    other = conn.execute("insert into tenant(name) values ('other') returning id").fetchone()["id"]
    assert get_approved(conn, tenant_id=other, plan_id=r.plan_id)["available"] is False
    with pytest.raises(PlanError, match="plan not found"):
        patch_draft(
            conn,
            tenant_id=other,
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1),
        )


def test_case_submission_validates():
    with pytest.raises(ValueError):
        CaseSubmission(case_ref="", intake=Intake())
