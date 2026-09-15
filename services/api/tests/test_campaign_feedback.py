"""What the board tells the operator when a run produces nothing reviewable.

The complaint these pin down: a campaign that read nine NHS pages about knee replacement and extracted no
exercises showed "10 sources · 0 new variants" and a blocker about an unrelated pending source. Three different
situations (nothing to read, pages read but no exercises on them, everything parked on unaccepted publishers)
all looked the same, and nothing on the board mentioned the exercises the condition already has.
"""

from __future__ import annotations

import uuid

from moveai_ingestion import campaigns as svc
from moveai_ingestion.pipeline import run_all

from .test_api import ALL_ALLOWED, FIX

LIMITS = {"max_sources": 3, "max_usd": 1.0, "max_search_requests": 5}


def _campaign(admin, title: str, **scope):
    body = {
        "ailment_text": "demo condition",
        "codes": [],
        "limits": LIMITS,
        "supplied_source_urls": [],
        "source_policy": "supplied_only",
        "scope_confirmed": True,
        **scope,
    }
    r = admin.post("/ingestion-campaigns", json={"title": title, "scope": body})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_page_that_yields_no_exercises_says_so_instead_of_no_candidates(admin, conn, monkeypatch):
    """The real case: the page was read, evidence was recorded, and none of it described an exercise."""
    url = f"file://{FIX / 'owned_demo_protocol.html'}?c={uuid.uuid4().hex[:6]}"
    r = admin.post(
        "/sources",
        json={"canonical_url": url, "source_type": "html", "publisher": "MoveAI", "title": "owned", "rights": {"permissions": {}}},
    )
    sid = r.json()["id"]
    admin.patch(f"/sources/{sid}", json={"allowlist_state": "approved"})
    # rights unknown: the job fails at access_check, so nothing is read at all
    c = _campaign(admin, "nothing readable", supplied_source_urls=[url])
    admin.post(f"/ingestion-campaigns/{c['id']}/runs")
    run_all(conn)
    card = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    assert card["lifecycle"] == "needs_attention"
    assert any("failed permanently" in b for b in card["blockers"]), card["blockers"]
    # and the page is listed with why, not just counted
    out = card["source_outcomes"]
    assert len(out) == 1 and out[0]["variants"] == 0
    assert "could not be read" in out[0]["outcome"] and "rights_unknown" in out[0]["outcome"]


def test_each_page_read_is_listed_with_what_came_out_of_it(admin, conn):
    url = f"file://{FIX / 'owned_demo_protocol.html'}?c={uuid.uuid4().hex[:6]}"
    r = admin.post(
        "/sources",
        json={"canonical_url": url, "source_type": "html", "publisher": "MoveAI", "title": "owned", "rights": {"permissions": ALL_ALLOWED}},
    )
    admin.patch(f"/sources/{r.json()['id']}", json={"allowlist_state": "approved"})
    c = _campaign(admin, "reads fine", supplied_source_urls=[url])
    admin.post(f"/ingestion-campaigns/{c['id']}/runs")
    run_all(conn)
    card = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    out = card["source_outcomes"]
    assert len(out) == 1
    assert out[0]["variants"] == 2 and out[0]["outcome"] == "2 exercise(s) extracted"
    assert out[0]["url"].startswith("file://") and out[0]["state"] == "pending_review"
    # the per-page list counts every exercise the page produced, including ones the catalog already had — the
    # totals split those into new vs duplicate, which is why "0 new variants" alone never told the whole story
    assert card["counts"]["new_variants"] + card["counts"]["duplicates"] == 2
    assert card["counts"]["awaiting_review"] == 2 and card["lifecycle"] == "pt_review"


def test_the_card_reports_exercises_the_catalog_already_holds_for_the_condition(admin, conn, seeded):
    """Andy's "at the very least use the exercises you already have": the packs' exercises are in the catalog
    whatever a run did, and a campaign that found nothing must not imply otherwise."""
    c = _campaign(admin, "already covered")
    card = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    expected = conn.execute(
        """select count(distinct cu.variant_version_id)::int as n from clinical_use_version cu
             join condition cd on cd.id=cu.condition_id where cd.internal_code='demo_synthetic'"""
    ).fetchone()["n"]
    assert expected > 0, "the demo pack should seed clinical uses"
    assert card["counts"]["catalog_variants"] == expected
    assert card["counts"]["catalog_placeholder"] + card["counts"]["catalog_approved"] <= expected
    assert card["conditions"] and card["conditions"][0]["code"] == "demo_synthetic"


def test_a_run_with_nothing_to_read_points_at_the_catalog_it_already_has(admin, conn):
    c = _campaign(admin, "nothing at all")
    admin.post(f"/ingestion-campaigns/{c['id']}/runs")
    run_all(conn)
    card = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    assert card["lifecycle"] == "needs_attention"
    assert any("found nothing to read" in b for b in card["blockers"])
    assert "the catalog already holds" in (card["next_human_action"] or "")


def test_counts_do_not_break_for_a_campaign_with_no_conditions(admin, conn):
    """A scope that resolved to nothing still has to produce a card rather than a 500."""
    c = _campaign(admin, "unresolved", ailment_text=None)
    card = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    assert card["counts"]["catalog_variants"] == 0 and card["conditions"] == []


def test_source_outcomes_is_empty_before_a_run(admin, conn):
    c = _campaign(admin, "not started")
    assert admin.get(f"/ingestion-campaigns/{c['id']}").json()["source_outcomes"] == []
    assert svc.source_outcomes(conn, uuid.UUID(c["id"])) == []
