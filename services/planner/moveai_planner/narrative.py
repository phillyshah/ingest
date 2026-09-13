"""Deterministic narrative pre-extraction. Produces *proposed* intake fields; safety-critical fields stay unconfirmed
until a PT confirms them (spec §8). No model call: the MVP extracts only unambiguous surface facts."""

from __future__ import annotations

import re
from typing import Any

from moveai_contracts.enums import FieldStatus
from moveai_contracts.intake import Intake, IntakeField
from moveai_contracts.matching import condition_names, name_matches

# Fields a narrative may propose but never establish on its own.
CRITICAL_FIELDS = {
    "affected_side",
    "diagnosis_confirmation",
    "procedure",
    "procedure_type",
    "surgeon_restrictions",
    "severity",
    "irritability",
    "concerning_findings",
    "prior_session_response",
    "associated_procedures",
    "brace_restrictions",
    "weight_bearing_restrictions",
    "rom_restrictions",
}

SEX_WORDS = {
    "man": "male",
    "male": "male",
    "gentleman": "male",
    "woman": "female",
    "female": "female",
    "lady": "female",
}
BODY_SIZE_WORDS = (
    "slightly obese",
    "obese",
    "overweight",
    "heavy",
    "slim",
    "underweight",
    "thin",
    "athletic build",
)


def extract(narrative: str | None) -> tuple[dict[str, IntakeField], list[str]]:
    """Returns (proposed fields, assumptions/notes)."""
    fields: dict[str, IntakeField] = {}
    notes: list[str] = []
    if not narrative:
        return fields, notes
    text = narrative.strip()
    low = text.lower()
    m = re.search(r"(\d{1,3})\s*(?:-|\s)?\s*(?:year|yr|y)[\s-]*(?:old|o)\b", low) or re.search(r"\bage\s*(\d{1,3})\b", low)
    if m:
        fields["age"] = IntakeField(
            status=FieldStatus.known,
            value=int(m.group(1)),
            unit="years",
            provenance="narrative_extracted",
            reported_text=m.group(0),
        )
    for w, v in SEX_WORDS.items():
        if re.search(rf"\b{w}\b", low):
            fields["sex"] = IntakeField(status=FieldStatus.known, value=v, provenance="narrative_extracted", reported_text=w)
            notes.append(f"Sex recorded from the word {w!r}; gender not disclosed and not inferred.")
            break
    for w in BODY_SIZE_WORDS:
        if w in low:
            fields["bmi"] = IntakeField(status=FieldStatus.unknown, value=None, provenance="narrative_extracted", reported_text=w)
            notes.append(f"Body size preserved as reported text {w!r}; no BMI class inferred without height and weight.")
            break
    side = re.search(r"\b(right|left)\b", low)
    if side:
        fields["affected_side"] = IntakeField(
            status=FieldStatus.unknown,
            value=None,
            provenance="narrative_extracted",
            reported_text=side.group(1),
        )
        notes.append(f"Laterality {side.group(1)!r} appears in the narrative; it is proposed, not established, until the PT confirms.")
    if re.search(r"\b(surgery|surgical|repair|reconstruction|operat)", low):
        fields["procedure"] = IntakeField(
            status=FieldStatus.unknown,
            value=None,
            provenance="narrative_extracted",
            reported_text="surgical wording present",
        )
        notes.append("Surgical wording present; exact procedure, date, and restrictions must be confirmed.")
    if re.search(r"\b(no surgery|nonoperative|non-operative|without surgery|conservative)", low):
        fields["procedure"] = IntakeField(
            status=FieldStatus.unknown,
            value=None,
            provenance="narrative_extracted",
            reported_text="nonoperative wording present",
        )
        notes.append("Nonoperative wording present; treat as proposed until confirmed.")
    return fields, notes


def merge(base: Intake, proposed: dict[str, IntakeField]) -> Intake:
    """PT-entered fields win; proposed fields fill gaps. Critical proposed fields never become known."""
    out = dict(base.fields)
    for k, f in proposed.items():
        if k in out and out[k].status != FieldStatus.unknown:
            continue
        if k in CRITICAL_FIELDS and f.status == FieldStatus.known:
            f = f.model_copy(update={"status": FieldStatus.unknown})
        out[k] = f
    return Intake(fields=out)


def candidate_conditions(conditions: list[dict[str, Any]], narrative: str | None, intake: Intake) -> list[dict[str, Any]]:
    text = " ".join(
        x
        for x in [
            narrative or "",
            str(intake.value("diagnosis") or ""),
            str(intake.value("candidate_condition") or ""),
        ]
        if x
    ).lower()
    if not text:
        return []
    hits = []
    for c in conditions:
        if any(name_matches(n, text) for n in condition_names(c)):
            hits.append(c)
    return hits
