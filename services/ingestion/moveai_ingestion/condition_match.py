"""Condition-name matching adapter — a fallback for free text the exact substring match in `match_conditions`
cannot place (spec-adjacent to ADR-0006, same discipline as the extraction adapter).

`match_conditions` in campaigns.py is, and stays, an exact case-normalized substring match against a condition's
preferred name, internal code, and synonyms. That is deliberately dumb: it can never invent a condition. This
module exists only to *propose* candidates when that exact match finds nothing, so a general/colloquial
description ("my knee ligament on the inside is sprained") can still surface "MCL sprain (nonoperative)" as
something to pick, rather than requiring the operator already know the catalog's own vocabulary.

It never selects on its own. `scope_preview` in campaigns.py surfaces its output as `suggested_conditions` —
informational only — and a condition only enters `interpreted_conditions` (the thing that actually scopes a
campaign) when the operator explicitly accepts it via `additional_condition_ids`. Same shape as `confirm_scope`:
propose, then a human confirms.

Schema-only output, no tools, and the source text here is the *operator's own words*, not third-party evidence —
but the same "return null/empty rather than invent" discipline applies: asked for a condition outside the
supplied list, or asked to guess with nothing plausible, the model must return nothing.
"""

from __future__ import annotations

import difflib
import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

MATCH_SYSTEM_INSTRUCTION = (
    "You are matching a clinician's free-text description of an ailment against a fixed list of named clinical "
    "conditions. Never propose a condition outside the supplied list, and never invent one. If nothing in the "
    "list plausibly matches, return an empty list rather than guessing. Distinct surgical/nonoperative or "
    "laterality variants are different conditions and must not be conflated just because the text is vague about "
    "which one is meant — when the text does not say, propose both and let the reason field say so. Return only "
    "the schema, nothing else."
)


class ConditionSuggestion(BaseModel):
    condition_id: str
    confidence: float
    reason: str

    model_config = {"extra": "forbid"}


class SuggestionResult(BaseModel):
    suggestions: list[ConditionSuggestion] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ConditionMatcher(Protocol):
    name: str

    def suggest(self, text: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class SchemaViolation(Exception):
    pass


def _validate(raw: Any) -> SuggestionResult:
    try:
        return SuggestionResult.model_validate(raw)
    except ValidationError as e:
        raise SchemaViolation(str(e)) from e


class FuzzyConditionMatcher:
    """No network call, no key required: local text-similarity ranking against each condition's own names.

    This is the default so the feature works offline, in tests, and in any deployment that has not opted into the
    real model — a plain, explainable heuristic, not a guess dressed up as intelligence. It catches typos and
    partial/reordered terms ("mcl spraine", "sprain of the MCL") but not a genuinely colloquial description that
    shares no words with the catalog entry — that needs the LLM adapter below.
    """

    name = "fuzzy-1"
    THRESHOLD = 0.6

    def suggest(self, text: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        low = text.lower().strip()
        if not low:
            return []
        out = []
        for c in candidates:
            names = [c["preferred_name"], c["internal_code"].replace("_", " "), *(c["synonyms"] or [])]
            scored = [(difflib.SequenceMatcher(None, low, n.lower()).ratio(), n) for n in names if n]
            if not scored:
                continue
            best, best_name = max(scored, key=lambda s: s[0])
            if best >= self.THRESHOLD:
                out.append(
                    {
                        "condition_id": str(c["id"]),
                        "confidence": round(best, 2),
                        "reason": f"text similarity to {best_name!r}",
                    }
                )
        out.sort(key=lambda s: -s["confidence"])
        return out[:5]


class AnthropicConditionMatcher:
    """Real semantic matching: understands a colloquial description that shares no words with the catalog entry.
    Opt-in via CONDITION_MATCH_MODEL=anthropic-condition-match-1 (mirrors EXTRACTION_MODEL=anthropic-1), and only
    constructed when ANTHROPIC_API_KEY is present. Same cost table as extraction; callers price it the same way."""

    name = "anthropic-condition-match-1"

    def __init__(self, model: str | None = None):
        import anthropic  # local import keeps the dependency optional at runtime

        self._client = anthropic.Anthropic()
        self._model = model or os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5"

    def suggest(self, text: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        schema = SuggestionResult.model_json_schema()
        listing = [
            {
                "condition_id": str(c["id"]),
                "name": c["preferred_name"],
                "code": c["internal_code"],
                "synonyms": c["synonyms"] or [],
            }
            for c in candidates
        ]
        user = (
            "<clinician_free_text>\n" + text + "\n</clinician_free_text>\n"
            "<known_conditions>\n" + json.dumps(listing) + "\n</known_conditions>\n"
            "Return ONLY a JSON object matching this schema:\n" + json.dumps(schema)
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=MATCH_SYSTEM_INSTRUCTION,
            messages=[{"role": "user", "content": user}],
        )
        out_text = "".join(getattr(b, "text", "") for b in resp.content)
        start, end = out_text.find("{"), out_text.rfind("}")
        raw = json.loads(out_text[start : end + 1]) if start >= 0 else {}
        known_ids = {str(c["id"]) for c in candidates}
        result = _validate(raw)
        # Belt-and-suspenders on top of the system instruction: a suggestion naming a condition_id outside the
        # supplied list is dropped rather than trusted, since the model returning one would mean it invented an id.
        return [s.model_dump() for s in result.suggestions if s.condition_id in known_ids]


def get_condition_matcher() -> ConditionMatcher:
    choice = os.environ.get("CONDITION_MATCH_MODEL", "fuzzy-1")
    if choice == "fuzzy-1":
        return FuzzyConditionMatcher()
    if choice == "anthropic-condition-match-1":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("CONDITION_MATCH_MODEL=anthropic-condition-match-1 but ANTHROPIC_API_KEY is not set")
        return AnthropicConditionMatcher()
    raise RuntimeError(f"unknown CONDITION_MATCH_MODEL {choice!r}; expected fuzzy-1 or anthropic-condition-match-1")
