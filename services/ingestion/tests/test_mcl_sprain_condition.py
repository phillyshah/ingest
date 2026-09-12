"""The nonoperative MCL sprain pack, and the diagnosis-code ambiguity it exposed.

`grade 3 MCL sprain` used to resolve to no condition at all: the only MCL pack shipped was `mcl_repair`, a
post-surgical reconstruction pathway, and routing a nonoperative sprain into it would have been clinically wrong
even if the text had matched. This pack fills that gap.

Adding it surfaced a real bug in `conditions_for_codes`, not in the pack: S83.411A ("sprain of the medial
collateral ligament") is the same diagnosis code whether or not the sprain is later treated surgically — ICD-10
does not encode procedure — so a bare code can never honestly select between the two pathways. `mcl_repair`
already tagged its mapping `broader` for exactly that reason; `conditions_for_codes` just was not honouring it.
"""

from __future__ import annotations

import pytest
from moveai_ingestion.campaigns import conditions_for_codes, match_conditions, scope_preview
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.packs import install_pack
from moveai_rules import load_pack


@pytest.fixture()
def with_mcl_packs(conn, users):
    for name in ("mcl_sprain_nonoperative", "mcl_repair"):
        install_pack(conn, load_pack(FIXTURES / "content-packs" / f"{name}.yaml"), author_id=users["pt"])
    return conn


def test_grade_3_mcl_sprain_resolves_to_the_nonoperative_pack(with_mcl_packs, conn):
    matched = match_conditions(conn, "grade 3 MCL sprain")
    assert [c["internal_code"] for c in matched] == ["mcl_sprain_nonoperative"]


def test_surgical_language_still_resolves_to_the_repair_pack_only(with_mcl_packs, conn):
    matched = match_conditions(conn, "surgically repaired MCL tear")
    assert [c["internal_code"] for c in matched] == ["mcl_tear_surgical_repair"]


def test_a_bare_sprain_code_does_not_pick_a_pathway(with_mcl_packs, conn):
    """The code alone cannot tell operative from nonoperative, so it must not silently pick either one."""
    assert conditions_for_codes(conn, ["S83.411A"]) == []


def test_the_two_mcl_pathways_are_never_simultaneously_ambiguous_for_a_plain_report(with_mcl_packs, conn):
    """A campaign scoped from ordinary free text about a sprain must actually be startable, not stuck as
    'ambiguous scope: 2 conditions matched' because a shared diagnosis code pulled in the surgical pack too."""
    out = scope_preview(
        conn,
        None,
        {
            "ailment_text": "grade 2 MCL sprain",
            "codes": [],
            "limits": {
                "max_sources": 5,
                "max_candidates": 5,
                "max_search_requests": 5,
                "max_runtime_seconds": 600,
                "max_usd": 1,
                "max_document_bytes": 1_000_000,
            },
            "supplied_source_urls": [],
            "scope_confirmed": False,
            "exclusion": {},
            "refinements": {},
        },
    )
    assert [c["code"] for c in out["interpreted_conditions"]] == ["mcl_sprain_nonoperative"]
    assert not any("ambiguous" in b for b in out["blocking_reasons"])
