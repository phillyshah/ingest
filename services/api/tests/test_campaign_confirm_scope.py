"""Confirming a campaign's scope so it can start (spec §21B).

Why this file exists: the confirmation gate was enforced in the API but only reachable from a checkbox inside the
New campaign dialog, which appeared only after pressing "Preview scope". A campaign saved as a draft without that
could never be confirmed afterwards — Start returned "confirm the scope preview before starting" and there was
nowhere to confirm it. The backend was right and the door was missing. These tests cover the door.
"""

from __future__ import annotations

from typing import Any

SCOPE: dict[str, Any] = {
    "ailment_text": "frozen shoulder",
    "codes": [],
    "limits": {
        "max_sources": 5,
        "max_candidates": 10,
        "max_search_requests": 5,
        "max_runtime_seconds": 600,
        "max_usd": 1,
        "max_document_bytes": 1_000_000,
    },
    "supplied_source_urls": [],
    "acceptance_criteria": ["phases covered"],
}


def draft(admin, confirmed: bool = False) -> dict[str, Any]:
    r = admin.post("/ingestion-campaigns", {"title": "frozen shoulder", "scope": {**SCOPE, "scope_confirmed": confirmed}})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_draft_saved_without_confirming_says_so(admin):
    c = draft(admin)
    assert c["lifecycle"] == "draft"
    assert "the scope has not been confirmed" in c["blockers"]
    # The card has to name the next step, because the board is where someone looks first.
    assert "confirm" in (c["next_human_action"] or "")
    assert "confirm_scope" in c["allowed_actions"]


def test_starting_an_unconfirmed_campaign_is_refused_with_a_usable_message(admin):
    c = draft(admin)
    r = admin.post(f"/ingestion-campaigns/{c['id']}/runs")
    assert r.status_code == 422
    assert r.json()["code"] == "scope_not_confirmed"
    assert "confirm" in r.json()["message"]


def test_the_preview_is_readable_before_confirming(admin):
    """You cannot honestly confirm an interpretation you were never shown."""
    c = draft(admin)
    r = admin.post(f"/ingestion-campaigns/{c['id']}/scope-preview")
    assert r.status_code == 200
    body = r.json()
    assert "interpreted_conditions" in body
    assert "scope not confirmed" in body["blocking_reasons"]


def test_confirming_then_starting_works(admin):
    c = draft(admin)
    r = admin.post(f"/ingestion-campaigns/{c['id']}/confirm-scope")
    assert r.status_code == 200, r.text
    assert "the scope has not been confirmed" not in r.json()["blockers"]
    assert "confirm_scope" not in r.json()["allowed_actions"]
    assert admin.post(f"/ingestion-campaigns/{c['id']}/runs").status_code == 202


def test_confirming_twice_is_not_an_error(admin):
    c = draft(admin)
    assert admin.post(f"/ingestion-campaigns/{c['id']}/confirm-scope").status_code == 200
    assert admin.post(f"/ingestion-campaigns/{c['id']}/confirm-scope").status_code == 200


def test_an_uninterpretable_scope_cannot_be_confirmed(admin):
    """Confirming is not a way past the other gates: authorizing spend on a request nobody understood is worse
    than refusing to start."""
    r = admin.post("/ingestion-campaigns", {"title": "vague", "scope": {**SCOPE, "ailment_text": "please fix my patient"}})
    cid = r.json()["id"]
    out = admin.post(f"/ingestion-campaigns/{cid}/confirm-scope")
    assert out.status_code == 422
    assert "no condition could be interpreted" in out.json()["message"]


def test_a_pt_cannot_confirm_a_scope(pt):
    """Confirming authorizes spending. It belongs to whoever owns the campaign, not to a reviewer."""
    assert pt.post("/ingestion-campaigns/00000000-0000-0000-0000-000000000000/confirm-scope").status_code == 403


def test_revising_the_scope_drops_the_confirmation(admin):
    """The person signed off on one interpretation, not on the campaign forever."""
    c = draft(admin, confirmed=True)
    assert "the scope has not been confirmed" not in c["blockers"]
    admin.patch(f"/ingestion-campaigns/{c['id']}", {"scope": {**SCOPE, "ailment_text": "MCL sprain", "scope_confirmed": False}})
    after = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    assert "the scope has not been confirmed" in after["blockers"]
    assert admin.post(f"/ingestion-campaigns/{c['id']}/runs").status_code == 422
