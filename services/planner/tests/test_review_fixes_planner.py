"""Planner-side fixes from the September 2026 backend review."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from _helpers import DEMO_COMPLETE
from moveai_contracts.dose import Dose, DoseField
from moveai_contracts.plan import DraftPatch
from moveai_planner.approval import PlanError, patch_draft
from moveai_planner.catalog import LoadedUse
from moveai_planner.engine import _media_for


def _use(media):
    return LoadedUse(id="u", row={}, variant={}, media=media, claims=[])


def _photo(**over):
    base = {
        "id": uuid.uuid4(),
        "media_state": "graphic_available",
        "can_display_to_patient": "allowed",
        "technique_review_passed": True,
        "revoked_at": None,
        "expires_at": None,
    }
    return {**base, **over}


def test_a_reviewed_photo_behind_a_reference_link_is_no_longer_hidden():
    """`_media_for` used to return on the first row, so a variant's older reference URL masked its real photo."""
    ref = {"id": uuid.uuid4(), "media_state": "reference_only"}
    photo = _photo()
    mid, state = _media_for(_use([ref, photo]))
    assert mid == str(photo["id"]) and state == "graphic_available"


def test_an_unreviewed_photo_still_never_reaches_a_plan():
    mid, state = _media_for(_use([_photo(technique_review_passed=None)]))
    assert mid is None and state == "graphic_available"  # the state is reported; the id is not handed out


def test_expired_or_revoked_rights_block_even_a_reviewed_photo():
    assert _media_for(_use([_photo(revoked_at=datetime.now(UTC))]))[0] is None
    assert _media_for(_use([_photo(expires_at=datetime.now(UTC) - timedelta(days=1))]))[0] is None


def test_no_media_means_not_requested():
    assert _media_for(_use([])) == (None, "not_requested")


# ---------------------------------------------------------------- draft patches cannot launder provenance
def test_a_draft_patch_cannot_cite_a_claim_that_does_not_exist(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="prov-1")
    assert r.status == "draft_ready"
    opt = r.options[0]
    item = opt.items[0].model_copy(deep=True)
    item.prescribed_dose = Dose(
        kind="prescribed",
        fields={"sets": DoseField(value=3, unit="count", provenance="source_explicit", claim_id=str(uuid.uuid4()))},
    )
    with pytest.raises(PlanError, match="unknown_claim"):
        patch_draft(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, items=[item]),
        )


def test_a_draft_patch_cannot_replace_the_approved_instructions(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="prov-2")
    opt = r.options[0]
    item = opt.items[0].model_copy(deep=True)
    item.instructions = ["Do whatever you like."]
    with pytest.raises(PlanError, match="instructions_diverge"):
        patch_draft(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, items=[item]),
        )


def test_a_clinician_authored_dose_in_a_draft_must_name_the_editor(conn, seeded, run):
    r = run("demo condition", fields=DEMO_COMPLETE, case_ref="prov-3")
    opt = r.options[0]
    item = opt.items[0].model_copy(deep=True)
    item.prescribed_dose = Dose(
        kind="prescribed",
        fields={"sets": DoseField(value=3, unit="count", provenance="clinician_authored", author_id=str(seeded["lead"]))},
    )
    with pytest.raises(PlanError, match="author_mismatch"):
        patch_draft(
            conn,
            tenant_id=seeded["tenant_id"],
            user_id=seeded["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, items=[item]),
        )
    item.prescribed_dose.fields["sets"] = DoseField(value=3, unit="count", provenance="clinician_authored", author_id=str(seeded["pt"]))
    row = patch_draft(
        conn,
        tenant_id=seeded["tenant_id"],
        user_id=seeded["pt"],
        plan_id=r.plan_id,
        patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, items=[item]),
    )
    assert row["revision"] == 2
