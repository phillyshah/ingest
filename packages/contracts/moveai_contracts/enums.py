"""Enums mirroring db/migrations/0001_foundation.sql. A test asserts parity with the SQL enums."""

from enum import StrEnum


class PermissionState(StrEnum):
    allowed = "allowed"
    denied = "denied"
    unknown = "unknown"


class ApprovalState(StrEnum):
    draft = "draft"
    unsigned_placeholder = "unsigned_placeholder"
    pending_review = "pending_review"
    approved = "approved"
    published = "published"
    invalidated = "invalidated"
    rejected = "rejected"
    withdrawn = "withdrawn"
    superseded = "superseded"


class PipelineState(StrEnum):
    discovered = "discovered"
    access_checked = "access_checked"
    fetched = "fetched"
    parsed = "parsed"
    extracted = "extracted"
    normalized = "normalized"
    evidence_linked = "evidence_linked"
    pending_review = "pending_review"
    approved = "approved"
    published = "published"
    rights_hold = "rights_hold"
    parse_failed = "parse_failed"
    extraction_failed = "extraction_failed"
    conflict_hold = "conflict_hold"
    rejected = "rejected"
    superseded = "superseded"
    withdrawn = "withdrawn"


class MediaState(StrEnum):
    not_requested = "not_requested"
    reference_only = "reference_only"
    graphic_available = "graphic_available"
    rights_hold = "rights_hold"
    unavailable = "unavailable"


class AssistanceMode(StrEnum):
    passive = "passive"
    active_assisted = "active_assisted"
    active = "active"
    resisted = "resisted"


class DoseProvenance(StrEnum):
    source_explicit = "source_explicit"
    clinician_authored = "clinician_authored"
    not_applicable = "not_applicable"
    unknown = "unknown"


class FieldStatus(StrEnum):
    known = "known"
    unknown = "unknown"
    not_assessed = "not_assessed"
    not_applicable = "not_applicable"


class ApplicabilityRelationship(StrEnum):
    studied_population = "studied_population"
    guideline_recommended_population = "guideline_recommended_population"
    supported_modification = "supported_modification"
    precaution = "precaution"
    contraindication = "contraindication"
    clinician_consensus = "clinician_consensus"
    insufficient_evidence = "insufficient_evidence"
    conflicting_evidence = "conflicting_evidence"


class ApplicabilityAction(StrEnum):
    inform_only = "inform_only"
    rank = "rank"
    request_assessment = "request_assessment"
    adapt = "adapt"
    monitor = "monitor"
    exclude = "exclude"


class SupportCategory(StrEnum):
    direct_guideline_or_research = "direct_guideline_or_research"
    indirect_support = "indirect_support"
    local_clinical_consensus = "local_clinical_consensus"
    conflicting_support = "conflicting_support"
    insufficient_support = "insufficient_support"


class UserRole(StrEnum):
    source_admin = "source_admin"
    rights_reviewer = "rights_reviewer"
    pt = "pt"
    clinical_lead = "clinical_lead"
    auditor = "auditor"
    integration = "integration"


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead_letter = "dead_letter"
    cancelled = "cancelled"


class PlanStatus(StrEnum):
    needs_assessment = "needs_assessment"
    blocked_for_clinical_review = "blocked_for_clinical_review"
    draft_ready = "draft_ready"


class PlanVersionStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"
    review_required = "review_required"
    withdrawn = "withdrawn"


class CampaignLifecycle(StrEnum):
    draft = "draft"
    queued = "queued"
    running = "running"
    needs_attention = "needs_attention"
    pt_review = "pt_review"
    complete = "complete"
    closed_incomplete = "closed_incomplete"


class CampaignControl(StrEnum):
    active = "active"
    paused = "paused"
    cancelled = "cancelled"


class RunState(StrEnum):
    pending = "pending"
    running = "running"
    paused = "paused"
    cancelled = "cancelled"
    finished = "finished"


class ReviewDecision(StrEnum):
    accept = "accept"
    edit = "edit"
    split = "split"
    merge = "merge"
    reject = "reject"
    request_clarification = "request_clarification"
    approve = "approve"
    withdraw = "withdraw"
    rights_allow = "rights_allow"
    rights_deny = "rights_deny"
    # A PT/lead confirming a stored picture actually shows the exercise correctly (spec §20A). Distinct from
    # approving the media version: rights say the picture *may* be shown; technique review says it *should* be.
    technique_review = "technique_review"


SQL_ENUM_MAP: dict[str, type[StrEnum]] = {
    "permission_state": PermissionState,
    "approval_state": ApprovalState,
    "pipeline_state": PipelineState,
    "media_state": MediaState,
    "assistance_mode": AssistanceMode,
    "dose_provenance": DoseProvenance,
    "field_status": FieldStatus,
    "applicability_relationship": ApplicabilityRelationship,
    "applicability_action": ApplicabilityAction,
    "support_category": SupportCategory,
    "user_role": UserRole,
    "job_state": JobState,
    "plan_status": PlanStatus,
    "plan_version_status": PlanVersionStatus,
    "campaign_lifecycle": CampaignLifecycle,
    "campaign_control": CampaignControl,
    "run_state": RunState,
    "review_decision": ReviewDecision,
}
