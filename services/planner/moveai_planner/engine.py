"""Plan-options engine (spec §8 generation algorithm, §20B). Deterministic; no model calls.

Routing:
  needs_assessment            -> missing safety-critical inputs; previews shown; prescription=null; assignable=false
  blocked_for_clinical_review -> an approved/placeholder rule detected a concern, restriction, unsupported presentation,
                                 or no approved pathway exists
  draft_ready                 -> up to three coherent options, each validated; approval is a separate PT action
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
from moveai_contracts.dose import Dose, DoseField
from moveai_contracts.enums import FieldStatus, PlanStatus
from moveai_contracts.intake import CaseSubmission, Intake
from moveai_contracts.plan import (
    BlockedInfo,
    ExcludedCandidate,
    MissingField,
    PathwayPreview,
    PlanItem,
    PlanOption,
    PlanOptionsResponse,
    Reassessment,
    SourceReference,
    TimelineRow,
    TimeWindow,
    ValidationResult,
)
from moveai_db import J
from moveai_rules.ast import Tri, evaluate

from . import narrative as nar
from .catalog import LoadedProtocol, LoadedUse, all_conditions, latest_release, load_protocols
from .segmentation import derive_observations, ensure_bmi_definition, explain_segments

WEIGHTS = {"presentation": 0.35, "goal": 0.25, "evidence": 0.20, "practical": 0.20}
MAX_OPTIONS = 3
PREVIEW_LABEL = "Protocol preview—requires assessment and PT approval"


def content_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------- rule evaluation
def evaluate_rules(protocols: list[LoadedProtocol], intake: Intake) -> dict[str, Any]:
    """Runs every pack rule once. Returns missing fields, blocks, exclusions (executable only), rank adjustments, informs."""
    missing: dict[str, MissingField] = {}
    blocks: list[BlockedInfo] = []
    exclusions: list[tuple[dict, str, str]] = []  # (action, rule_id, rule_name)
    informs: list[str] = []
    rank_adjust: list[tuple[float, str]] = []
    unsigned_slots: list[str] = []
    seen: set[str] = set()
    for p in protocols:
        for rid, lr in p.rules.items():
            if rid in seen:
                continue
            seen.add(rid)
            r = lr.rule
            ev = evaluate(r.expression, intake)
            act = r.action
            if act.type == "require_field":
                if ev.result is not Tri.TRUE:
                    for f in [act.field] if act.field else act.fields:
                        # a field is "addressed" when known or explicitly not applicable; unknown/not_assessed are not
                        if f and f not in missing and intake.status(f) not in (FieldStatus.known, FieldStatus.not_applicable):
                            missing[f] = MissingField(field=f, reason=act.reason or f"required by {r.name}", required_by=r.name)
                continue
            if r.severity == "block" and act.type == "block_for_review":
                if ev.result is Tri.TRUE:
                    blocks.append(BlockedInfo(rule_version_id=rid, rule_name=r.name, message=act.message or r.name))
                elif ev.result is Tri.UNKNOWN:
                    # an uncertain safety-critical result stops prescription generation (spec §8 step 2)
                    for f in ev.unknown_fields:
                        missing.setdefault(
                            f,
                            MissingField(
                                field=f,
                                reason=f"needed to evaluate safety rule {r.name!r}; unknown is not a negative finding",
                                required_by=r.name,
                            ),
                        )
                continue
            if act.type == "exclude_variant":
                if ev.result is Tri.TRUE:
                    if lr.executable:
                        exclusions.append((act.model_dump(), rid, r.name))
                    else:
                        unsigned_slots.append(
                            f"{r.name}: would exclude {act.variant_keys or act.tags or act.assistance} but the rule is {lr.approval_state}; not applied"
                        )
                continue
            if act.type == "rank" and ev.result is Tri.TRUE and lr.executable:
                rank_adjust.append((act.adjustment or 0.0, r.name))
            if act.type == "inform" and ev.result is Tri.TRUE and r.kind not in ("eligibility", "progression"):
                informs.append(act.message or r.name)
    return {
        "missing": list(missing.values()),
        "blocks": blocks,
        "exclusions": exclusions,
        "informs": informs,
        "rank_adjust": rank_adjust,
        "unsigned_slots": unsigned_slots,
    }


def protocol_eligible(p: LoadedProtocol, intake: Intake) -> Tri:
    results = []
    for rid in p.eligibility_rule_ids:
        lr = p.rules.get(rid)
        if not lr:
            return Tri.UNKNOWN
        results.append(evaluate(lr.rule.expression, intake).result)
    if not results:
        return Tri.TRUE
    if any(r is Tri.FALSE for r in results):
        return Tri.FALSE
    return Tri.TRUE if all(r is Tri.TRUE for r in results) else Tri.UNKNOWN


# ---------------------------------------------------------------- building blocks
def _window(w: dict[str, Any]) -> TimeWindow:
    return TimeWindow(
        anchor=w.get("anchor", "assessment"),
        start=w.get("start"),
        end=w.get("end"),
        unit=w.get("unit", "weeks"),
        provisional=bool(w.get("provisional", True)),
        text=w.get("text"),
        provenance=w.get("provenance", "unknown"),
    )


def _source_refs(u: LoadedUse) -> list[SourceReference]:
    refs = []
    sr = u.variant.get("source_reference") or {}
    if sr:
        refs.append(
            SourceReference(
                url=sr.get("url"),
                publisher=sr.get("publisher"),
                title=sr.get("title"),
                document_version=sr.get("document_version"),
                locator=sr.get("locator"),
                source_exercise_name=sr.get("source_exercise_name"),
            )
        )
    for c in u.claims:
        refs.append(
            SourceReference(
                url=c["canonical_url"],
                publisher=c["publisher"],
                title=c["source_title"],
                document_version=c["document_identity"],
                locator=c["locator"],
                evidence_claim_id=str(c["id"]),
            )
        )
    return refs


def _media_for(u: LoadedUse) -> tuple[str | None, str]:
    """Media is optional. Patient display needs allowed rights + passed technique review; otherwise text only."""
    for m in u.media:
        if (
            m["media_state"] == "graphic_available"
            and m.get("can_display_to_patient") == "allowed"
            and m.get("technique_review_passed")
            and not m.get("revoked_at")
            and not (m.get("expires_at") and m["expires_at"] <= datetime.now(UTC))
        ):
            return str(m["id"]), "graphic_available"
        return None, m["media_state"]
    return None, "not_requested"


def _dose_from_envelope(u: LoadedUse, *, prescribed: bool) -> Dose:
    fields = {k: DoseField.model_validate(v) for k, v in (u.row["dose_envelope"] or {}).items()}
    return Dose(kind="prescribed" if prescribed else "envelope", fields=fields)


def _item(u: LoadedUse, *, prescribed: bool) -> PlanItem:
    media_id, media_state = _media_for(u)
    dose = _dose_from_envelope(u, prescribed=prescribed)
    has_numbers = any(f.is_set for f in dose.fields.values())
    return PlanItem(
        variant_version_id=str(u.variant["id"]),
        variant_name=u.variant["name"],
        clinical_use_version_id=u.id,
        media_version_id=media_id,
        media_state=media_state,
        instructions=[s["text"] for s in (u.variant["step_sequence"] or [])],
        prescribed_dose=dose,
        dose_label=("prescribed" if prescribed and has_numbers else "protocol_example" if has_numbers else "none"),
        goal_ids=list(u.row["goals"] or []),
        evidence_claim_ids=[str(c) for c in u.row["supporting_claim_ids"]],
        source_references=_source_refs(u),
        rationale=u.row.get("indication") or u.row.get("clinician_rationale"),
        cautions=[u.row["exclusion"]] if u.row.get("exclusion") else [],
    )


def _timeline(p: LoadedProtocol, intake: Intake, *, prescribed_phase: str | None) -> list[TimelineRow]:
    rows = []
    restrictions_known = intake.known("surgeon_restrictions")
    for ph in p.phases:
        items = [_item(p.uses[uid], prescribed=(ph["key"] == prescribed_phase)) for uid in ph["items"] if uid in p.uses]
        restrictions = list(ph.get("restrictions") or [])
        if restrictions_known and intake.value("surgeon_restrictions"):
            restrictions.append(f"patient-specific: {intake.value('surgeon_restrictions')}")
        rows.append(
            TimelineRow(
                phase_key=ph["key"],
                phase_name=ph["name"],
                goals=ph.get("goals") or [],
                time_window=_window(ph.get("window") or {}),
                exercises=items,
                schedule_note=(
                    "approved doses"
                    if any(i.dose_label == "prescribed" for i in items)
                    else "protocol example values"
                    if any(i.dose_label == "protocol_example" for i in items)
                    else "dose not supplied by source or clinician; PT must author"
                ),
                restrictions=restrictions,
                unknown_restrictions=not restrictions_known,
                review_checkpoint=ph.get("review_checkpoint"),
                advance_criteria=ph.get("advance_criteria") or [],
                hold_criteria=ph.get("hold_criteria") or [],
                regress_criteria=ph.get("regress_criteria") or [],
                basis=[f"protocol {p.id} ({p.row['approval_state']})", *(ph.get("basis") or [])],
                gaps=list(ph.get("gaps") or []) + ([] if restrictions_known else ["restrictions unknown"]),
            )
        )
    return rows


def _preview(p: LoadedProtocol, intake: Intake) -> PathwayPreview:
    prov = p.row.get("provenance") or {}
    assumptions = [
        "Timing shown is relative and provisional; a scheduled date is a review opportunity, not proof of readiness.",
        "Numeric values, where present, are approved protocol examples, not an individualized prescription.",
    ]
    if p.row["approval_state"] not in ("approved", "published"):
        assumptions.append(f"This pathway is {p.row['approval_state']}: shown for orientation only; it cannot be prescribed.")
    return PathwayPreview(
        protocol_version_id=p.id,
        protocol_name=p.row["name"],
        condition=p.condition["name"],
        label=PREVIEW_LABEL,
        approval_state=p.row["approval_state"],
        branch_conditions=prov.get("branch_conditions") or [],
        timeline=_timeline(p, intake, prescribed_phase=None),
        recovery_horizon=(p.row.get("recovery_horizon") or {}).get("text"),
        assumptions=assumptions,
        source_references=[SourceReference.model_validate(r) for r in p.source_refs],
    )


# ---------------------------------------------------------------- eligibility, ranking, validation
def _hard_exclusions(p: LoadedProtocol, u: LoadedUse, intake: Intake, exclusions: list[tuple[dict, str, str]]) -> ExcludedCandidate | None:
    v = u.variant
    if not u.prescribable:
        return ExcludedCandidate(
            variant_version_id=str(v["id"]),
            protocol_version_id=p.id,
            reason=f"clinical use/variant is {u.row['approval_state']}/{v['approval_state']}; unpublished versions cannot enter a plan",
        )
    mods = v.get("supported_modifications") or {}
    tags = set(mods.get("tags", []))
    for act, rid, name in exclusions:
        if (
            str(v["id"]) in act.get("variant_keys", [])
            or v["name"] in act.get("variant_keys", [])
            or mods.get("pack_key") in act.get("variant_keys", [])
            or any(k and k in (v.get("source_reference") or {}).get("source_exercise_name", "") for k in act.get("variant_keys", []))
            or v["assistance"] in act.get("assistance", [])
            or tags & set(act.get("tags", []))
        ):
            return ExcludedCandidate(
                variant_version_id=str(v["id"]),
                protocol_version_id=p.id,
                reason=f"excluded by approved rule {name!r}",
                rule_version_id=rid,
            )
    equipment = intake.get("equipment")
    if equipment.is_known and v["equipment"]:
        have = {e.lower() for e in (equipment.value or [])}
        need = {e.lower() for e in v["equipment"]}
        if not need <= have:
            return ExcludedCandidate(
                variant_version_id=str(v["id"]),
                protocol_version_id=p.id,
                reason=f"requires equipment not available: {sorted(need - have)}",
            )
    pos = intake.get("can_assume_starting_position")
    if pos.is_known and pos.value is False and (v.get("supported_modifications") or {}).get("requires_starting_position"):
        return ExcludedCandidate(
            variant_version_id=str(v["id"]),
            protocol_version_id=p.id,
            reason="patient cannot assume the required starting position (observed limitation)",
        )
    return None


def _rank(p: LoadedProtocol, u: LoadedUse, intake: Intake) -> dict[str, float]:
    pres = u.row.get("presentation") or {}
    presentation = 0.0
    irr = intake.value("irritability")
    if pres.get("irritability") and irr:
        want = pres["irritability"]
        presentation = 1.0 if (irr == want or (want == "low_or_moderate" and irr in ("low", "moderate"))) else 0.0
    goals = intake.get("goals")
    goal = 0.0
    if goals.is_known and u.row["goals"]:
        pg = {g.lower() for g in goals.value or []}
        goal = len(pg & {g.lower() for g in u.row["goals"]}) / max(1, len(u.row["goals"]))
    evidence = {"direct": 1.0, "indirect": 0.5}.get(u.row["directness"], 0.0)
    practical = 0.0
    eq = intake.get("equipment")
    if eq.is_known:
        practical += 0.5 if {e.lower() for e in u.variant["equipment"]} <= {e.lower() for e in (eq.value or [])} else 0.0
    if "home" in (u.variant["setting"] or []):
        practical += 0.5
    total = (
        WEIGHTS["presentation"] * presentation + WEIGHTS["goal"] * goal + WEIGHTS["evidence"] * evidence + WEIGHTS["practical"] * practical
    )
    return {
        "presentation_fit": presentation,
        "goal_fit": goal,
        "evidence_relevance": evidence,
        "practical_fit": practical,
        "score": round(total, 4),
    }


def _current_phase(p: LoadedProtocol, intake: Intake) -> str | None:
    """Phases are ordered. The current phase is the furthest phase reachable through entry criteria that are all TRUE
    on this assessment; the walk stops at the first phase whose criteria are not met. Elapsed time never advances a
    phase (spec §8 time horizon, §13 test 11)."""
    current: str | None = None
    for ph in p.phases:
        ok = True
        for rid in ph.get("entry_rules") or []:
            lr = p.rules.get(rid)
            if not lr or evaluate(lr.rule.expression, intake).result is not Tri.TRUE:
                ok = False
                break
        if not ok:
            break
        current = ph["key"]
    return current


def validate_option(opt: PlanOption, intake: Intake) -> ValidationResult:
    blockers: list[str] = []
    warnings: list[str] = []
    joints: dict[str, int] = {}
    total_min = 0.0
    for it in opt.items:
        d = it.prescribed_dose or Dose()
        for f in d.unsupported_numbers():
            blockers.append(f"{it.variant_name}: dose field {f} has a number without approved provenance")
        for f in d.missing_critical():
            blockers.append(f"{it.variant_name}: execution-critical dose field {f} is unknown; blocks approval")
        if not it.source_references:
            blockers.append(f"{it.variant_name}: no source reference")
        if not it.instructions:
            blockers.append(f"{it.variant_name}: no instructions")
        est = 0.0
        sets = d.fields.get("sets")
        reps = d.fields.get("repetitions")
        hold = d.fields.get("hold_seconds")
        if sets and sets.is_set and reps and reps.is_set:
            est = float(sets.value or 0) * float(reps.value or 0) * (float(hold.value or 0) if hold and hold.is_set else 3.0) / 60.0
        total_min += est
    # whole-plan checks (spec §13 test 17)
    for it in opt.items:
        j = f"{it.variant_name.split('(')[0].strip().lower()}"
        joints[j] = joints.get(j, 0) + 1
    for j, n in joints.items():
        if n > 1:
            warnings.append(f"duplicate loading: {j!r} appears {n} times")
    limit = intake.get("session_time_minutes")
    if limit.is_known and total_min and total_min > float(limit.value):
        blockers.append(f"estimated session burden {total_min:.0f} min exceeds available {limit.value} min")
    opt.estimated_session_minutes = round(total_min, 1) if total_min else None
    if intake.status("surgeon_restrictions") != FieldStatus.known:
        blockers.append("restrictions unknown; unknown is not equivalent to no restrictions")
    return ValidationResult(passed=not blockers, blockers=blockers, warnings=warnings)


def narrative_for(opt: PlanOption) -> str:
    """Deterministic plain-language summary built only from structured data (no LLM in MVP)."""
    lines = [f"Option {opt.label}: {len(opt.items)} exercise(s)."]
    for it in opt.items:
        d = it.prescribed_dose
        nums = ", ".join(f"{k}={f.value}" for k, f in (d.fields.items() if d else []) if f.is_set)
        lines.append(f"- {it.variant_name}: {nums or 'dose not supplied'}; goal(s): {', '.join(it.goal_ids) or 'n/a'}.")
    return "\n".join(lines)


def narrative_matches(opt: PlanOption, narrative: str) -> bool:
    """Every number in the prescription must appear in the narrative and vice-versa (spec §8 step 7, §13 test 16)."""
    import re

    want = set()
    for it in opt.items:
        for f in it.prescribed_dose.fields.values() if it.prescribed_dose else []:
            if f.is_set and f.value is not None:
                want.add(str(f.value).rstrip("0").rstrip(".") if isinstance(f.value, float) else str(f.value))
    have = {n.rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", narrative)}
    return want <= have


# ---------------------------------------------------------------- main entry
def plan_options(conn: psycopg.Connection, *, tenant_id: Any, user_id: Any, submission: CaseSubmission) -> PlanOptionsResponse:
    proposed, notes = nar.extract(submission.narrative)
    intake = nar.merge(submission.intake, proposed)
    conditions = all_conditions(conn)
    candidates = nar.candidate_conditions(conditions, submission.narrative, intake)
    release = None
    if submission.catalog_release_id:
        release = conn.execute("select * from catalog_release where id=%s", (submission.catalog_release_id,)).fetchone()
        if not release:
            raise ValueError("catalog_release_not_found")
    else:
        release = latest_release(conn)

    # case snapshot (patient store, tenant scoped)
    prev = conn.execute(
        "select coalesce(max(revision),0) as r from case_snapshot where tenant_id=%s and case_ref=%s",
        (tenant_id, submission.case_ref),
    ).fetchone()["r"]
    snap = conn.execute(
        """insert into case_snapshot(tenant_id, case_ref, revision, assessment_time, assessor_user_id, narrative, intake, completeness)
           values (%s,%s,%s,%s,%s,%s,%s,%s) returning id, revision""",
        (
            tenant_id,
            submission.case_ref,
            prev + 1,
            submission.assessment_time,
            user_id,
            submission.narrative,
            J({k: v.model_dump(mode="json") for k, v in intake.fields.items()}),
            J({"known": [k for k, v in intake.fields.items() if v.is_known]}),
        ),
    ).fetchone()
    observations = derive_observations(conn, tenant_id, snap["id"], intake)
    bmi_def = ensure_bmi_definition(conn)

    resp = PlanOptionsResponse(
        status=PlanStatus.needs_assessment,
        case_snapshot_id=str(snap["id"]),
        case_revision=snap["revision"],
        candidate_conditions=[c["preferred_name"] for c in candidates],
        assumptions=notes,
        catalog_release_id=str(release["id"]) if release else None,
    )
    if not candidates:
        resp.missing_fields = [
            MissingField(
                field="diagnosis",
                reason="No candidate condition recognized; state the confirmed or suspected diagnosis and assessor",
            )
        ]
        resp.notes.append("No approved source-backed pathway matches this input; nothing was fabricated.")
        return _persist(conn, tenant_id, user_id, snap, resp, intake)

    protocols = load_protocols(
        conn,
        [c["id"] for c in candidates],
        release_id=release["id"] if release else None,
        include_unsigned=True,
    )
    applicability = [a for p in protocols for a in p.applicability] + [
        a for p in protocols for u in p.uses.values() for a in u.applicability
    ]
    resp.segment_explanations = explain_segments(observations, applicability, f"{bmi_def['authority']} {bmi_def['authority_version']}")
    resp.pathway_previews = [_preview(p, intake) for p in protocols]
    anchors = {w.anchor for pv in resp.pathway_previews for r in pv.timeline for w in [r.time_window]}
    resp.timeline_anchor = (
        ", ".join(sorted(f"{a} ({'known' if intake.known(a + '_date') or a == 'assessment' else 'date unknown'})" for a in anchors)) or None
    )
    resp.phase_windows = [r.time_window for pv in resp.pathway_previews for r in pv.timeline]
    if not protocols:
        resp.notes.append("No approved source-backed pathway exists for this condition; say so rather than fabricate one.")
        resp.status = PlanStatus.blocked_for_clinical_review
        resp.blocked = BlockedInfo(
            rule_version_id=None,
            rule_name="no_pathway",
            message="No pathway (approved or placeholder) exists for the recognized condition.",
        )
        return _persist(conn, tenant_id, user_id, snap, resp, intake)

    rules = evaluate_rules(protocols, intake)
    resp.notes.extend(rules["unsigned_slots"])
    if rules["blocks"]:
        resp.status = PlanStatus.blocked_for_clinical_review
        resp.blocked = rules["blocks"][0]
        resp.missing_fields = rules["missing"]
        return _persist(conn, tenant_id, user_id, snap, resp, intake)
    if rules["missing"]:
        resp.status = PlanStatus.needs_assessment
        resp.missing_fields = rules["missing"]
        resp.requires_pt_review = True
        resp.assignable = False
        return _persist(conn, tenant_id, user_id, snap, resp, intake)

    # complete assessment: only prescribable protocols in the pinned release may produce drafts
    eligible = [p for p in protocols if p.prescribable and protocol_eligible(p, intake) is Tri.TRUE]
    excluded: list[ExcludedCandidate] = []
    for p in protocols:
        if not p.prescribable:
            excluded.append(ExcludedCandidate(protocol_version_id=p.id, reason=f"pathway is {p.row['approval_state']}; not prescribable"))
        elif protocol_eligible(p, intake) is not Tri.TRUE:
            excluded.append(ExcludedCandidate(protocol_version_id=p.id, reason="eligibility rules not satisfied for this presentation"))
    resp.excluded_candidates = excluded
    if not eligible:
        resp.status = PlanStatus.blocked_for_clinical_review
        resp.blocked = BlockedInfo(
            rule_version_id=None,
            rule_name="no_approved_pathway",
            message="Assessment is complete but no approved, published pathway supports this presentation. Clinical lead review required.",
        )
        return _persist(conn, tenant_id, user_id, snap, resp, intake)

    options: list[PlanOption] = []
    for p in eligible[:MAX_OPTIONS]:
        phase = _current_phase(p, intake)
        if phase is None:
            excluded.append(
                ExcludedCandidate(
                    protocol_version_id=p.id,
                    reason="no phase entry criteria are met on the current assessment",
                )
            )
            continue
        items: list[PlanItem] = []
        ranks: list[dict[str, float]] = []
        ph = next(x for x in p.phases if x["key"] == phase)
        for uid in ph["items"]:
            u = p.uses.get(uid)
            if not u:
                continue
            ex = _hard_exclusions(p, u, intake, rules["exclusions"])
            if ex:
                excluded.append(ex)
                continue
            it = _item(u, prescribed=True)
            r = _rank(p, u, intake)
            for adj, name in rules["rank_adjust"]:
                r["score"] = round(r["score"] + adj, 4)
                r[f"adjust:{name}"] = adj
            items.append(it)
            ranks.append(r)
        if not items:
            excluded.append(ExcludedCandidate(protocol_version_id=p.id, reason="every candidate item was excluded by hard filters"))
            continue
        order = sorted(range(len(items)), key=lambda i: -ranks[i]["score"])
        items = [items[i] for i in order]
        ranks = [ranks[i] for i in order]
        opt = PlanOption(
            option_id=f"opt-{uuid.uuid4().hex[:8]}",
            label=p.row["name"],
            case_snapshot_id=str(snap["id"]),
            catalog_release_id=str(release["id"]) if release else None,
            protocol_version_ids=[p.id],
            rule_version_ids=sorted(p.rules),
            items=items,
            timeline=_timeline(p, intake, prescribed_phase=phase),
            monitoring_rules=list(p.row["monitoring"] or []),
            reassessment=Reassessment(**((p.row.get("provenance") or {}).get("reassessment") or {"timing": None})),
            progression_requirements=[c for x in p.phases for c in (x.get("advance_criteria") or [])],
            equipment=sorted({e for it in items for e in p.uses[it.clinical_use_version_id].variant["equipment"]}),
            uncovered_goals=sorted({g for g in (intake.value("goals") or [])} - {g for it in items for g in it.goal_ids}),
            evidence_limitations=[f"{it.variant_name}: {p.uses[it.clinical_use_version_id].row['support_category']}" for it in items],
            rank_explanation={it.variant_version_id: r for it, r in zip(items, ranks, strict=True)},
        )
        opt.validation = validate_option(opt, intake)
        opt.content_hash = content_hash(opt.model_dump(mode="json", exclude={"content_hash", "option_id", "validation"}))
        options.append(opt)
    resp.excluded_candidates = excluded
    if not options:
        resp.status = PlanStatus.blocked_for_clinical_review
        resp.blocked = BlockedInfo(
            rule_version_id=None,
            rule_name="no_valid_items",
            message="No approved variant survived hard exclusions; PT authoring required.",
        )
        return _persist(conn, tenant_id, user_id, snap, resp, intake)
    resp.status = PlanStatus.draft_ready
    resp.options = options
    resp.assignable = False  # only an approved revision is assignable
    resp.requires_pt_review = True
    return _persist(conn, tenant_id, user_id, snap, resp, intake)


def _persist(
    conn: psycopg.Connection,
    tenant_id: Any,
    user_id: Any,
    snap: dict,
    resp: PlanOptionsResponse,
    intake: Intake,
) -> PlanOptionsResponse:
    plan_id = uuid.uuid4()
    payload = resp.model_dump(mode="json")
    row = conn.execute(
        """insert into plan_version(tenant_id, plan_id, revision, case_snapshot_id, catalog_release_id, status, routing, options, validation,
             protocol_version_ids, rule_version_ids, content_hash, author_id, segment_explanations)
           values (%s,%s,1,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (
            tenant_id,
            plan_id,
            snap["id"],
            resp.catalog_release_id,
            resp.status.value,
            J(payload["options"]),
            J(
                {
                    "status": resp.status.value,
                    "missing": payload["missing_fields"],
                    "blocked": payload["blocked"],
                }
            ),
            [uuid.UUID(x) for o in resp.options for x in o.protocol_version_ids],
            [uuid.UUID(x) for o in resp.options for x in o.rule_version_ids],
            content_hash(payload["options"]),
            user_id,
            J(payload["segment_explanations"]),
        ),
    ).fetchone()
    resp.plan_id = str(plan_id)
    resp.notes.append(f"plan_version {row['id']} revision 1 ({resp.status.value})")
    return resp
