"""Remaining API DTOs: sources, jobs, reviews, protocols, releases, changes, campaigns, errors (spec §10, §21F)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import (
    ApprovalState,
    CampaignControl,
    CampaignLifecycle,
    JobState,
    PermissionState,
    PipelineState,
    ReviewDecision,
    RunState,
    UserRole,
)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Any = None


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
    total: int | None = None


PERMISSION_OPS: tuple[str, ...] = (
    "can_fetch", "can_store_fulltext", "can_store_excerpt", "can_embed_for_search", "can_process_with_model",
    "can_store_transcript", "can_download_media", "can_display_to_clinician", "can_display_to_patient",
    "can_redistribute", "can_transform", "can_train_model",
)


class RightsInput(BaseModel):
    permissions: dict[str, PermissionState] = Field(default_factory=dict)
    attribution_required: bool | None = None
    attribution_text: str | None = None
    territory: str | None = None
    expires_at: datetime | None = None
    permission_evidence: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(BaseModel):
    canonical_url: str
    publisher: str | None = None
    title: str | None = None
    source_type: str = Field(pattern="^(html|pdf|scanned_pdf|clinician_upload|sitemap|feed|api)$")
    policy_reference: str | None = None
    review_cadence_days: int | None = None
    intended_uses: list[str] = Field(default_factory=list)
    crawl_limits: dict[str, Any] = Field(default_factory=dict)
    rights: RightsInput | None = None
    local_path: str | None = None       # clinician upload / permitted fixture; absolute path under fixtures/


class SourceOut(BaseModel):
    id: str
    canonical_url: str
    publisher: str | None
    title: str | None
    source_type: str
    allowlist_state: str
    latest_version_id: str | None = None
    latest_pipeline_state: PipelineState | None = None
    created_at: datetime


class IngestionJobCreate(BaseModel):
    source_id: str
    campaign_id: str | None = None
    campaign_run_id: str | None = None
    force_refetch: bool = False


class IngestionJobOut(BaseModel):
    id: str
    source_version_id: str | None
    stage: str
    state: JobState
    attempts: int
    warnings: list[Any] = Field(default_factory=list)
    error_class: str | None = None
    last_error: str | None = None
    cost_usd: float = 0
    duration_ms: int | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    campaign_id: str | None = None
    campaign_run_id: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    pipeline_state: PipelineState | None = None
    retry_eligible: bool = False


class ReviewCreate(BaseModel):
    entity_table: str = Field(pattern="^(exercise_variant_version|clinical_use_version|protocol_version|rule_version|"
                              "media_asset_version|diagnosis_mapping_version|population_applicability_version|"
                              "rights_grant|source_version)$")
    version_id: str
    decision: ReviewDecision
    reason: str | None = None
    changes: dict[str, Any] | None = None
    time_spent_seconds: int | None = None
    expected_approval_state: ApprovalState | None = None
    merge_into_entity_id: str | None = None
    campaign_id: str | None = None


class ReviewOut(BaseModel):
    review_event_id: str
    entity_table: str
    version_id: str
    new_version_id: str | None = None
    approval_state: ApprovalState
    invalidated_prior_approval: bool = False


class ProtocolCreate(BaseModel):
    condition_code: str
    name: str
    population_description: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    setting: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    monitoring: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)   # typed rule ASTs, validated
    recovery_horizon: dict[str, Any] | None = None
    tenant_private: bool = True


class CatalogReleaseCreate(BaseModel):
    label: str
    include_tables: list[str] = Field(default_factory=lambda: [
        "exercise_variant_version", "clinical_use_version", "protocol_version", "rule_version",
        "media_asset_version", "population_applicability_version", "diagnosis_mapping_version",
        "segmentation_definition_version"])


class CatalogReleaseOut(BaseModel):
    id: str
    label: str
    manifest_sha256: str
    item_count: int
    published_at: datetime


class ChangeOut(BaseModel):
    cursor: int
    change_type: str
    entity_table: str
    version_id: str
    release_id: str | None
    created_at: datetime


# ---- campaigns (§21) ----
class CampaignLimits(BaseModel):
    max_search_requests: int = Field(ge=0, default=30)
    max_sources: int = Field(ge=0, default=20)
    max_document_bytes: int = Field(ge=0, default=50_000_000)
    max_candidates: int = Field(ge=0, default=40)
    max_runtime_seconds: int = Field(ge=0, default=7200)
    max_usd: float = Field(ge=0, default=10.0)


class CampaignScopeInput(BaseModel):
    ailment_text: str | None = None
    codes: list[str] = Field(default_factory=list)
    desired_output: str = "text_first_phased_plan_templates"
    priority: int = 100
    source_policy: str = Field(default="allowlist_only", pattern="^(allowlist_only|supplied_only|allowlist_and_supplied)$")
    limits: CampaignLimits = Field(default_factory=CampaignLimits)
    acceptance_criteria: list[str] = Field(default_factory=list)
    media_policy: str = Field(default="reference_only", pattern="^(none|reference_only|small_graphics)$")
    refinements: dict[str, Any] = Field(default_factory=dict)
    supplied_source_urls: list[str] = Field(default_factory=list)
    exclusion: dict[str, Any] = Field(default_factory=dict)
    scope_confirmed: bool = False
    service_date: str | None = None


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    owner_id: str | None = None
    reviewer_id: str | None = None
    target_date: str | None = None
    scope: CampaignScopeInput


class ResolvedCode(BaseModel):
    code: str
    descriptor: str | None
    release_id: str | None
    release_label: str | None
    laterality: str | None
    billable: bool | None
    resolved: bool
    note: str | None = None


class ScopePreview(BaseModel):
    interpreted_conditions: list[dict[str, Any]]
    resolved_codes: list[ResolvedCode]
    unresolved_text: list[str]
    included: dict[str, Any]
    excluded: dict[str, Any]
    existing_coverage: dict[str, Any]
    missing: list[str]
    proposed_strategy: list[str]
    max_work: CampaignLimits
    clinical_review_requirements: list[str]
    can_start: bool
    blocking_reasons: list[str]


class CampaignCard(BaseModel):
    id: str
    title: str
    lifecycle: CampaignLifecycle
    control: CampaignControl
    priority: int
    owner: str | None
    reviewer: str | None
    condition_summary: str | None
    scope_summary: str | None
    current_activity: str | None
    last_update: datetime
    heartbeat_at: datetime | None
    heartbeat_stale: bool
    counts: dict[str, int]
    spend_usd: float
    cap_usd: float
    active_seconds: int
    coverage: dict[str, int]
    blockers: list[str]
    next_human_action: str | None
    allowed_actions: list[str]
    run_id: str | None
    run_revision: int | None


class CampaignDetail(CampaignCard):
    scope: dict[str, Any]
    runs: list[dict[str, Any]]
    coverage_checks: list[dict[str, Any]]
    limits: CampaignLimits
    budget: dict[str, Any]
    warnings: list[str]


class RunAction(BaseModel):
    expected_revision: int
    reason: str | None = None


class CampaignPatch(BaseModel):
    title: str | None = None
    priority: int | None = None
    reviewer_id: str | None = None
    owner_id: str | None = None
    scope: CampaignScopeInput | None = None
    maintenance_enabled: bool | None = None


class Me(BaseModel):
    user_id: str
    tenant_id: str | None
    roles: list[UserRole]
    display_name: str | None = None


class RunOut(BaseModel):
    id: str
    run_number: int
    state: RunState
    revision: int
    started_at: datetime | None
    finished_at: datetime | None
    stop_reason: str | None
    budget: dict[str, Any]
    progress: dict[str, Any]
