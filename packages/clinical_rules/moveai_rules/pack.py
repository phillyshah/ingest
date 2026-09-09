"""Content-pack schema and loader (ADR-0007). A pack carries all clinical content with provenance and approval state.
Shipped packs are unsigned_placeholder; the publisher refuses to publish them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from moveai_contracts.dose import Dose, DoseField
from moveai_contracts.enums import ApprovalState, AssistanceMode
from pydantic import BaseModel, Field, model_validator

from .ast import Rule


class PackSource(BaseModel):
    key: str
    url: str
    publisher: str
    title: str
    document_version: str | None = None
    source_type: str = "pdf"
    rights: dict[str, str] = Field(default_factory=dict)  # permission -> allowed|denied|unknown
    rights_evidence: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class PackClaim(BaseModel):
    key: str
    source: str
    locator: dict[str, Any]
    claim_type: str
    paraphrase: str
    population: dict[str, Any] | None = None
    recommendation_strength_as_reported: str | None = None
    limitations: str | None = None


class PackVariant(BaseModel):
    key: str
    name: str
    assistance: AssistanceMode
    region: str
    joint: str | None = None
    movement_plane: str | None = None
    starting_position: str | None = None
    load_mode: str | None = None
    side_behavior: str = "unilateral"
    equipment: list[str] = Field(default_factory=list)
    setting: list[str] = Field(default_factory=lambda: ["home"])
    step_sequence: list[str] = Field(default_factory=list)
    cues: list[str] = Field(default_factory=list)
    common_errors: list[str] = Field(default_factory=list)
    target_impairment: list[str] = Field(default_factory=list)
    functional_goal: list[str] = Field(default_factory=list)
    balance_demand: str | None = None
    tags: list[str] = Field(default_factory=list)
    requires_starting_position: bool = False
    source_reference: dict[str, Any] | None = None  # {source, locator, source_exercise_name}
    media_state: str = "not_requested"
    approval_state: ApprovalState = ApprovalState.unsigned_placeholder


class PackConcept(BaseModel):
    key: str
    preferred_name: str
    aliases: list[str] = Field(default_factory=list)
    body_region: str
    movement_purpose: str | None = None
    variants: list[PackVariant]


class PackClinicalUse(BaseModel):
    key: str
    variant: str
    phase: str
    indication: str | None = None
    exclusion: str | None = None
    goals: list[str] = Field(default_factory=list)
    presentation: dict[str, Any] = Field(default_factory=dict)
    dose_envelope: dict[str, dict[str, Any]] = Field(default_factory=dict)
    supporting_claims: list[str] = Field(default_factory=list)
    directness: str = "unknown"
    support_category: str | None = None
    clinician_rationale: str | None = None
    session_minutes_estimate: float | None = None
    approval_state: ApprovalState = ApprovalState.unsigned_placeholder

    def dose(self) -> Dose:
        return Dose(kind="envelope", fields={k: DoseField.model_validate(v) for k, v in self.dose_envelope.items()})


class PackPhase(BaseModel):
    key: str
    name: str
    goals: list[str] = Field(default_factory=list)
    window: dict[str, Any] = Field(default_factory=dict)
    items: list[str] = Field(default_factory=list)  # clinical_use keys
    entry_rules: list[str] = Field(default_factory=list)
    advance_criteria: list[str] = Field(default_factory=list)
    hold_criteria: list[str] = Field(default_factory=list)
    regress_criteria: list[str] = Field(default_factory=list)
    review_checkpoint: str | None = None
    restrictions: list[str] = Field(default_factory=list)
    monitoring: list[str] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class PackProtocol(BaseModel):
    key: str
    name: str
    population_description: str | None = None
    branch_conditions: list[str] = Field(default_factory=list)
    eligibility_rules: list[str] = Field(default_factory=list)  # rule keys: TRUE => eligible; UNKNOWN => needs inputs
    required_inputs: list[str] = Field(default_factory=list)
    setting: list[str] = Field(default_factory=lambda: ["home", "supervised"])
    phases: list[PackPhase]
    monitoring: list[str] = Field(default_factory=list)
    reassessment: dict[str, Any] | None = None
    recovery_horizon: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    approval_state: ApprovalState = ApprovalState.unsigned_placeholder


class PackApplicability(BaseModel):
    key: str
    target: str  # clinical_use key or protocol key
    target_type: str  # clinical_use | protocol
    relationship_type: str
    predicates: list[dict[str, Any]]
    predicate_logic: str = "and"
    action_type: str = "inform_only"
    executable_rule: str | None = None
    support_category: str | None = None
    generalizability_limits: str | None = None
    evidence_claims: list[str] = Field(default_factory=list)
    clinician_rationale: str | None = None
    approval_state: ApprovalState = ApprovalState.unsigned_placeholder


class PackCondition(BaseModel):
    internal_code: str
    preferred_name: str
    synonyms: list[str] = Field(default_factory=list)
    body_region: str
    ambiguity_flags: list[str] = Field(default_factory=list)
    icd10cm: list[dict[str, Any]] = Field(default_factory=list)


class ContentPack(BaseModel):
    pack: str
    version: str
    family: str
    approval_state: ApprovalState = ApprovalState.unsigned_placeholder
    label: str
    clinical_lead_signature: str | None = None
    condition: PackCondition
    sources: list[PackSource] = Field(default_factory=list)
    claims: list[PackClaim] = Field(default_factory=list)
    concepts: list[PackConcept] = Field(default_factory=list)
    clinical_uses: list[PackClinicalUse] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    protocols: list[PackProtocol] = Field(default_factory=list)
    applicability: list[PackApplicability] = Field(default_factory=list)
    intake_prompts: dict[str, str] = Field(default_factory=dict)  # field -> reason text shown when missing

    @model_validator(mode="after")
    def _integrity(self) -> ContentPack:
        variants = {v.key for c in self.concepts for v in c.variants}
        uses = {u.key for u in self.clinical_uses}
        rules = {r.key for r in self.rules}
        claims = {c.key for c in self.claims}
        sources = {s.key for s in self.sources}
        for u in self.clinical_uses:
            if u.variant not in variants:
                raise ValueError(f"clinical use {u.key} references unknown variant {u.variant}")
            for ck in u.supporting_claims:
                if ck not in claims:
                    raise ValueError(f"clinical use {u.key} references unknown claim {ck}")
            u.dose()  # validates dose shape
        for c in self.claims:
            if c.source not in sources:
                raise ValueError(f"claim {c.key} references unknown source {c.source}")
        for p in self.protocols:
            for rk in p.eligibility_rules:
                if rk not in rules:
                    raise ValueError(f"protocol {p.key} references unknown rule {rk}")
            for ph in p.phases:
                for ik in ph.items:
                    if ik not in uses:
                        raise ValueError(f"phase {p.key}/{ph.key} references unknown clinical use {ik}")
                for rk in ph.entry_rules:
                    if rk not in rules:
                        raise ValueError(f"phase {p.key}/{ph.key} references unknown rule {rk}")
        for a in self.applicability:
            if a.action_type in ("adapt", "monitor", "exclude") and not a.executable_rule:
                raise ValueError(f"applicability {a.key}: {a.action_type} requires an executable rule")
            if a.executable_rule and a.executable_rule not in rules:
                raise ValueError(f"applicability {a.key} references unknown rule {a.executable_rule}")
        if self.approval_state in (ApprovalState.approved, ApprovalState.published) and not self.clinical_lead_signature:
            raise ValueError("approved/published packs require clinical_lead_signature")
        return self

    @property
    def signed(self) -> bool:
        return self.approval_state in (ApprovalState.approved, ApprovalState.published) and bool(self.clinical_lead_signature)

    def rule(self, key: str) -> Rule:
        return next(r for r in self.rules if r.key == key)

    def variant(self, key: str) -> PackVariant:
        return next(v for c in self.concepts for v in c.variants if v.key == key)

    def clinical_use(self, key: str) -> PackClinicalUse:
        return next(u for u in self.clinical_uses if u.key == key)


def load_pack(path: str | Path) -> ContentPack:
    data = yaml.safe_load(Path(path).read_text())
    return ContentPack.model_validate(data)


def load_all(directory: str | Path) -> list[ContentPack]:
    return [load_pack(p) for p in sorted(Path(directory).glob("*.yaml"))]
