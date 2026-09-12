"""Free-text scope input can be more general/colloquial than the catalog's exact wording (Andy's report: typing
"MCL sprain" worked, but he wanted to type something looser and have the system propose the closest match).

`match_conditions` stays an exact substring match — it never invents a condition. `suggested_conditions` is the
fallback, only reached when that exact match finds nothing, and it never selects on its own: a suggestion only
enters scope once the operator names its id in `additional_condition_ids`.
"""

from __future__ import annotations

import pytest
from moveai_ingestion.campaigns import match_conditions, scope_preview, suggested_conditions
from moveai_ingestion.condition_match import FuzzyConditionMatcher
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.packs import install_pack
from moveai_rules import load_pack

LIMITS = {
    "max_sources": 5,
    "max_candidates": 5,
    "max_search_requests": 5,
    "max_runtime_seconds": 600,
    "max_usd": 1,
    "max_document_bytes": 1_000_000,
}


@pytest.fixture()
def with_mcl_pack(conn, users):
    install_pack(conn, load_pack(FIXTURES / "content-packs" / "mcl_sprain_nonoperative.yaml"), author_id=users["pt"])
    return conn


def _preview(**scope):
    return {"ailment_text": None, "codes": [], "limits": LIMITS, "supplied_source_urls": [], "scope_confirmed": False, "exclusion": {}, "refinements": {}, **scope}


def test_a_typo_close_to_the_exact_wording_is_suggested_not_matched(with_mcl_pack, conn):
    """Close enough for the fuzzy fallback to propose, but not an exact substring, so it must not silently match."""
    assert match_conditions(conn, "MCL sprian") == []
    hits = suggested_conditions(conn, "MCL sprian")
    assert [h["code"] for h in hits] == ["mcl_sprain_nonoperative"]


def test_something_with_no_plausible_match_suggests_nothing(with_mcl_pack, conn):
    assert suggested_conditions(conn, "a completely unrelated shoulder complaint about the rotator cuff") == []


def test_suggestion_never_enters_scope_on_its_own(with_mcl_pack, conn):
    out = scope_preview(conn, None, _preview(ailment_text="MCL sprian"))
    assert out["interpreted_conditions"] == []
    assert [s["code"] for s in out["suggested_conditions"]] == ["mcl_sprain_nonoperative"]
    assert not any("ambiguous" in b for b in out["blocking_reasons"])
    assert "no condition could be interpreted from the ailment text or codes" in out["blocking_reasons"]


def test_accepting_a_suggestion_by_id_puts_it_in_scope(with_mcl_pack, conn):
    first = scope_preview(conn, None, _preview(ailment_text="MCL sprian"))
    cond_id = first["suggested_conditions"][0]["id"]
    out = scope_preview(conn, None, _preview(ailment_text="MCL sprian", additional_condition_ids=[cond_id]))
    assert [c["code"] for c in out["interpreted_conditions"]] == ["mcl_sprain_nonoperative"]
    assert not any("no condition could be interpreted" in b for b in out["blocking_reasons"])
    # Once accepted, it is no longer offered again as a suggestion — it already resolved.
    assert out["suggested_conditions"] == []


def test_a_successful_exact_match_never_triggers_the_fallback(with_mcl_pack, conn, monkeypatch):
    """The fallback only runs when the exact match comes up empty — it must never second-guess a match that
    already succeeded, which would risk pulling in an unrelated condition the operator never asked about."""
    called = False

    class Tripwire(FuzzyConditionMatcher):
        def suggest(self, *a, **kw):
            nonlocal called
            called = True
            return super().suggest(*a, **kw)

    import moveai_ingestion.campaigns as campaigns_module

    monkeypatch.setattr(campaigns_module, "get_condition_matcher", lambda: Tripwire())
    out = scope_preview(conn, None, _preview(ailment_text="grade 2 MCL sprain"))
    assert out["interpreted_conditions"][0]["code"] == "mcl_sprain_nonoperative"
    assert not called


def test_an_unknown_condition_id_from_the_matcher_is_dropped_not_trusted(with_mcl_pack, conn, monkeypatch):
    import moveai_ingestion.campaigns as campaigns_module

    class Dishonest:
        name = "dishonest-1"

        def suggest(self, text, candidates):
            return [{"condition_id": "00000000-0000-0000-0000-000000000000", "confidence": 0.99, "reason": "invented"}]

    monkeypatch.setattr(campaigns_module, "get_condition_matcher", lambda: Dishonest())
    assert suggested_conditions(conn, "anything") == []
