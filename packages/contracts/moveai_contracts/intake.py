"""Case intake contract (spec §8, §19). Every field distinguishes known / unknown / not_assessed / not_applicable.
A missing symptom report is not a negative finding."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import FieldStatus


class IntakeField(BaseModel):
    status: FieldStatus = FieldStatus.unknown
    value: Any = None
    unit: str | None = None
    provenance: str = "unknown"  # pt_entered | narrative_extracted | integration | derived
    confirmed_by: str | None = None  # app_user.id when a PT confirmed a narrative-extracted value
    reported_text: str | None = None  # verbatim user text preserved (e.g. "slightly obese")
    measured_at: datetime | None = None

    @classmethod
    def known(cls, value: Any, **kw: Any) -> IntakeField:
        return cls(status=FieldStatus.known, value=value, **kw)

    @property
    def is_known(self) -> bool:
        return self.status == FieldStatus.known and self.value is not None


# Canonical intake field names. The planner and content packs refer to these keys.
INTAKE_FIELDS: tuple[str, ...] = (
    "age",
    "sex",
    "gender",
    "height",
    "weight",
    "bmi",
    "diagnosis",
    "diagnosis_confirmation",
    "assessor",
    "candidate_condition",
    "affected_side",
    "onset_date",
    "onset_certainty",
    "procedure",
    "procedure_date",
    "procedure_type",
    "associated_procedures",
    "surgeon_restrictions",
    "brace_restrictions",
    "weight_bearing_restrictions",
    "rom_restrictions",
    "pain_rest",
    "pain_movement",
    "pain_night",
    "pain_scale",
    "symptom_behavior",
    "irritability",
    "severity",
    "injury_location",
    "rom_active",
    "rom_passive",
    "rom_measurement_context",
    "functional_limitations",
    "goals",
    "intended_activity",
    "comorbidities",
    "concerning_findings",
    "prior_interventions",
    "prior_injection",
    "exercise_tolerance",
    "next_day_response",
    "prior_session_response",
    "equipment",
    "accessibility_needs",
    "can_assume_starting_position",
    "session_time_minutes",
    "language",
    "functional_criteria_met",
    "assessment_timestamp",
    # Post-arthroplasty fields (September 2026 review, M1). The free-text restriction fields above stay for the
    # surgeon's own words; these are the structured facts a rule can actually test. Rules can only name fields in
    # this tuple (ast.Expr validation), which is why a pack could not add them itself.
    "weight_bearing_status",  # wbat | pwb | tdwb | nwb — surgeon-set, never defaulted
    "surgical_approach",  # posterior | anterior | lateral | other — decides hip precautions
    "fixation",  # cemented | uncemented | hybrid — bears on weight-bearing
    "knee_flexion_active_deg",
    "knee_extension_deficit_deg",  # degrees short of full extension; 0 = full
    "extension_lag_deg",  # quad lag on straight leg raise
    "hip_flexion_active_deg",
    "assistive_device",  # walker | crutches | cane | none
    "wound_status",  # healing | closed | concern
    "effusion",  # none | mild | moderate | severe
    "days_since_procedure",  # derived from procedure_date and the assessment time; never entered by hand
)

# Fields the planner derives itself. They are filled at plan time with provenance "derived" and are never
# proposed from a narrative or accepted from a client as "known".
DERIVED_FIELDS: tuple[str, ...] = ("days_since_procedure",)


class Intake(BaseModel):
    fields: dict[str, IntakeField] = Field(default_factory=dict)

    def get(self, name: str) -> IntakeField:
        return self.fields.get(name, IntakeField())

    def known(self, name: str) -> bool:
        return self.get(name).is_known

    def value(self, name: str, default: Any = None) -> Any:
        f = self.get(name)
        return f.value if f.is_known else default

    def status(self, name: str) -> FieldStatus:
        return self.get(name).status


class CaseSubmission(BaseModel):
    """POST /plan-options body."""

    case_ref: str = Field(min_length=1, max_length=120)
    narrative: str | None = None
    intake: Intake = Field(default_factory=Intake)
    assessment_time: datetime | None = None
    catalog_release_id: str | None = None  # pin; default = latest published
    service_date: str | None = None  # ISO date used to resolve terminology release
