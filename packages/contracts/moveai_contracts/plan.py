"""/plan-options response and plan option shapes (spec §8, §10, §20B)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .dose import Dose
from .enums import PlanStatus


class MissingField(BaseModel):
    field: str
    reason: str
    required_by: str | None = None       # protocol/rule name


class SourceReference(BaseModel):
    url: str | None = None
    publisher: str | None = None
    title: str | None = None
    document_version: str | None = None
    locator: dict[str, Any] | None = None
    source_exercise_name: str | None = None
    evidence_claim_id: str | None = None


class TimeWindow(BaseModel):
    anchor: str                          # onset | procedure | assessment | plan_start
    start: float | None = None
    end: float | None = None
    unit: str = "weeks"
    provisional: bool = True
    text: str | None = None              # preserved approximate wording from the source
    provenance: str = "unknown"


class TimelineRow(BaseModel):
    """One chronological row of an option (spec §20B table)."""

    phase_key: str
    phase_name: str
    goals: list[str] = Field(default_factory=list)
    time_window: TimeWindow
    exercises: list[PlanItem] = Field(default_factory=list)
    schedule_note: str | None = None
    restrictions: list[str] = Field(default_factory=list)
    unknown_restrictions: bool = True
    review_checkpoint: str | None = None
    advance_criteria: list[str] = Field(default_factory=list)
    hold_criteria: list[str] = Field(default_factory=list)
    regress_criteria: list[str] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class PlanItem(BaseModel):
    variant_version_id: str
    variant_name: str
    clinical_use_version_id: str
    media_version_id: str | None = None
    media_state: str = "not_requested"
    instructions: list[str] = Field(default_factory=list)
    prescribed_dose: Dose | None = None
    dose_label: str = "protocol_example"    # protocol_example | prescribed | none
    goal_ids: list[str] = Field(default_factory=list)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    rationale: str | None = None
    cautions: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    passed: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Reassessment(BaseModel):
    timing: str | None = None
    provenance: str = "unknown"
    checks: list[str] = Field(default_factory=list)


class PlanOption(BaseModel):
    option_id: str
    status: str = "draft"
    label: str
    case_snapshot_id: str | None = None
    catalog_release_id: str | None = None
    protocol_version_ids: list[str] = Field(default_factory=list)
    rule_version_ids: list[str] = Field(default_factory=list)
    items: list[PlanItem] = Field(default_factory=list)
    timeline: list[TimelineRow] = Field(default_factory=list)
    monitoring_rules: list[str] = Field(default_factory=list)
    reassessment: Reassessment | None = None
    progression_requirements: list[str] = Field(default_factory=list)
    estimated_session_minutes: float | None = None
    equipment: list[str] = Field(default_factory=list)
    uncovered_goals: list[str] = Field(default_factory=list)
    evidence_limitations: list[str] = Field(default_factory=list)
    differences_from_source: list[str] = Field(default_factory=list)
    rank_explanation: dict[str, Any] = Field(default_factory=dict)
    validation: ValidationResult = Field(default_factory=lambda: ValidationResult(passed=False))
    content_hash: str | None = None
    approval: dict[str, Any] | None = None


class PathwayPreview(BaseModel):
    """Conditional, time-based preview shown before individual prescribing is possible (spec §20B)."""

    protocol_version_id: str
    protocol_name: str
    condition: str
    label: str = "Protocol preview—requires assessment and PT approval"
    approval_state: str
    branch_conditions: list[str] = Field(default_factory=list)
    timeline: list[TimelineRow] = Field(default_factory=list)
    recovery_horizon: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)


class SegmentExplanation(BaseModel):
    dimension: str
    observed: str | None
    classification: str | None = None
    classification_version: str | None = None
    effect: str = "recorded for context; no supported modification applied"
    rule_version_id: str | None = None
    evidence_gap: str | None = None


class ExcludedCandidate(BaseModel):
    variant_version_id: str | None = None
    protocol_version_id: str | None = None
    reason: str
    rule_version_id: str | None = None


class BlockedInfo(BaseModel):
    rule_version_id: str | None
    rule_name: str
    message: str


class PlanOptionsResponse(BaseModel):
    status: PlanStatus
    case_snapshot_id: str | None = None
    case_revision: int = 1
    plan_id: str | None = None
    candidate_conditions: list[str] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    prescription: Any | None = None
    requires_pt_review: bool = True
    assignable: bool = False
    pathway_previews: list[PathwayPreview] = Field(default_factory=list)
    timeline_anchor: str | None = None
    phase_windows: list[TimeWindow] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    options: list[PlanOption] = Field(default_factory=list)
    blocked: BlockedInfo | None = None
    segment_explanations: list[SegmentExplanation] = Field(default_factory=list)
    excluded_candidates: list[ExcludedCandidate] = Field(default_factory=list)
    catalog_release_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class DraftPatch(BaseModel):
    expected_revision: int
    selected_option_id: str | None = None
    items: list[PlanItem] | None = None
    rationale: str | None = None
    edit_reason: str | None = None


class ApproveRequest(BaseModel):
    expected_revision: int
    attestation: str = Field(min_length=1)


class ReassessmentRequest(BaseModel):
    intake: Any
    narrative: str | None = None
    assessment_time: Any = None
