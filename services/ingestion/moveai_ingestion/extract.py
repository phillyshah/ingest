"""Extract stage: run the model, then deterministic validators that cannot be talked out of by the document."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .llm import ExtractionModel, ExtractionResult
from .parse import ParsedDocument

INJECTION_MARKERS = ("ignore previous instructions", "ignore all previous", "you are now", "call the", "system prompt")


@dataclass
class ExtractOutcome:
    result: ExtractionResult
    warnings: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


def _table_phase_map(doc: ParsedDocument) -> dict[str, str | None]:
    """Which phase heading encloses each table. Tables with no enclosing phase heading map to None."""
    out: dict[str, str | None] = {}
    for b in doc.blocks:
        if b.kind == "table" and b.locator.get("table"):
            phase = next((h for h in reversed(b.heading_path) if h.lower().startswith("phase")), None)
            out[b.locator["table"]] = phase
    return out


def run_extraction(doc: ParsedDocument, model: ExtractionModel, *, source_hint: str | None = None) -> ExtractOutcome:
    result, cost = model.extract(doc, source_hint=source_hint)
    out = ExtractOutcome(result=result, cost_usd=cost)
    text_lower = doc.text().lower()
    if any(m in text_lower for m in INJECTION_MARKERS):
        out.warnings.append("instruction-like text present in source; treated as data")
    table_phase = _table_phase_map(doc)
    for ex in result.exercises:
        if not ex.locator:
            out.review_flags.append(f"{ex.source_exercise_name}: missing locator")
        if ex.assistance is None:
            out.review_flags.append(f"{ex.source_exercise_name}: assistance mode not stated")
        for name, f in ex.dose.items():
            if f.value is not None and not f.locator:
                # a number without a locator has no provenance: drop it (spec §5.6)
                f.null_reason = f.null_reason or "value had no source locator; discarded"
                f.value = None
                out.review_flags.append(f"{ex.source_exercise_name}.{name}: number without locator discarded")
            if f.ambiguous or (f.ocr_confidence is not None and f.ocr_confidence < 0.8):
                f.value = None
                f.null_reason = f.null_reason or "ambiguous OCR"
                out.review_flags.append(f"{ex.source_exercise_name}.{name}: ambiguous OCR requires review")
            tbl = (f.locator or {}).get("table")
            if tbl and tbl in table_phase:
                phase_of_table = table_phase[tbl]
                if phase_of_table is None:
                    # heading removed: never let this dose inherit a neighbouring phase (spec §13 test 3)
                    if ex.phase is not None:
                        out.review_flags.append(f"{ex.source_exercise_name}: table {tbl} has no phase heading; phase link cleared")
                        ex.phase = None
                elif ex.phase and ex.phase != phase_of_table:
                    out.review_flags.append(f"{ex.source_exercise_name}: dose table {tbl} belongs to {phase_of_table!r}, not {ex.phase!r}")
                    ex.phase = None
    if result.unreadable:
        out.review_flags.append(f"{len(result.unreadable)} unreadable value(s) require review")
    if result.conflicts:
        out.review_flags.append(f"{len(result.conflicts)} conflicting statement(s)")
    return out
