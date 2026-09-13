"""Condition-name matching shared by campaign scoping and the planner's candidate search.

Exact, case-normalized substring matching — it never invents a condition (CLAUDE.md). One refinement: a short
name (an acronym such as THR, TKA, MCL) matches only as a whole word. "THR" is a substring of "arthroplasty", so
without this "total knee arthroplasty" resolved to the hip pack as well as the knee pack. Longer names keep plain
substring semantics so "knee replacement" still matches "knee replacements".
"""

from __future__ import annotations

import re

ACRONYM_MAX_LEN = 4


def name_matches(name: str | None, text: str) -> bool:
    if not name:
        return False
    n = name.lower()
    t = text.lower()
    if len(n) <= ACRONYM_MAX_LEN and n.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t) is not None
    return n in t


def condition_names(c: dict) -> list[str]:
    return [c["preferred_name"], (c["internal_code"] or "").replace("_", " "), *(c.get("synonyms") or [])]
