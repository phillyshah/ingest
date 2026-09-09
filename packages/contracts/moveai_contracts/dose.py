"""Dose schema (spec §6). Every field carries a typed value or range, unit, null reason, and provenance.
An absent number is never filled from model memory; it stays null with a reason."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .enums import DoseProvenance

DOSE_FIELDS: tuple[str, ...] = (
    "sets",
    "repetitions",
    "hold_seconds",
    "rest_seconds",
    "sessions_per_day",
    "days_per_week",
    "load_value",
    "load_unit",
    "tempo",
    "range_min_deg",
    "range_max_deg",
    "effort_scale",
    "effort_target",
    "session_minutes",
    "symptom_limit_rule_id",
    "next_day_response_rule_id",
)
# Fields whose absence blocks approval of an executable prescription when the exercise needs them (§8 step 6).
EXECUTION_CRITICAL: tuple[str, ...] = (
    "sets",
    "repetitions",
    "hold_seconds",
    "sessions_per_day",
    "days_per_week",
)


class Range(BaseModel):
    min: float | None = None
    max: float | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True


class DoseField(BaseModel):
    value: float | str | None = None
    range: Range | None = None
    unit: str | None = None
    null_reason: str | None = None
    provenance: DoseProvenance = DoseProvenance.unknown
    claim_id: str | None = None  # evidence_claim.id when provenance=source_explicit
    author_id: str | None = None  # app_user.id when provenance=clinician_authored

    @model_validator(mode="after")
    def _null_needs_reason(self) -> DoseField:
        if self.value is None and self.range is None and not self.null_reason:
            raise ValueError("a dose field without value or range must state null_reason")
        if self.provenance == DoseProvenance.source_explicit and (self.value is not None or self.range) and not self.claim_id:
            raise ValueError("source_explicit dose values must reference an evidence claim")
        if self.provenance == DoseProvenance.clinician_authored and (self.value is not None or self.range) and not self.author_id:
            raise ValueError("clinician_authored dose values must name the author")
        return self

    @property
    def is_set(self) -> bool:
        return self.value is not None or self.range is not None


class Dose(BaseModel):
    """Either a permitted envelope (ranges) or an exact prescription (values); `kind` says which."""

    kind: str = Field(default="envelope", pattern="^(envelope|prescribed)$")
    fields: dict[str, DoseField] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_fields(self) -> Dose:
        bad = set(self.fields) - set(DOSE_FIELDS)
        if bad:
            raise ValueError(f"unknown dose fields: {sorted(bad)}")
        return self

    def missing_critical(self) -> list[str]:
        return [f for f in EXECUTION_CRITICAL if f not in self.fields or not self.fields[f].is_set]

    def unsupported_numbers(self) -> list[str]:
        """Fields that hold a number without approved provenance (spec §13 test 12)."""
        out = []
        for name, f in self.fields.items():
            if f.is_set and f.provenance in (DoseProvenance.unknown, DoseProvenance.not_applicable):
                out.append(name)
        return out
