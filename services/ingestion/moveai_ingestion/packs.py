"""Install a content pack into the database (ADR-0007). Creates condition, sources (reference-only rights),
claims, concepts/variants, clinical uses, rules, protocols, and applicability rows with the pack's approval state.
Idempotent per (pack, version): reinstalling returns the existing rows."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg

from moveai_contracts.api import PERMISSION_OPS
from moveai_db import J
from moveai_rules.pack import ContentPack


def _pack_installed(conn: psycopg.Connection, pack: ContentPack) -> dict | None:
    return conn.execute("select id from protocol_version where content_pack=%s and content_pack_version=%s limit 1",
                        (pack.pack, pack.version)).fetchone()


def install_pack(conn: psycopg.Connection, pack: ContentPack, *, author_id: Any = None, approver_id: Any = None,
                 tenant_id: Any = None) -> dict[str, Any]:
    if _pack_installed(conn, pack):
        return summary(conn, pack)
    state = pack.approval_state.value
    approved = pack.signed
    if approved and (approver_id is None or approver_id == author_id):
        raise ValueError("approved packs need an approver distinct from the author")
    ids: dict[str, dict[str, Any]] = {"sources": {}, "claims": {}, "variants": {}, "uses": {}, "rules": {}, "protocols": {}}

    cond = conn.execute(
        """insert into condition(internal_code, preferred_name, synonyms, body_region, ambiguity_flags) values (%s,%s,%s,%s,%s)
           on conflict (internal_code) do update set synonyms = excluded.synonyms, ambiguity_flags = excluded.ambiguity_flags returning id""",
        (pack.condition.internal_code, pack.condition.preferred_name, pack.condition.synonyms, pack.condition.body_region,
         pack.condition.ambiguity_flags)).fetchone()
    cond_id = cond["id"]
    # diagnosis mappings against every loaded release that has the code
    for m in pack.condition.icd10cm:
        for tc in conn.execute("select id from terminology_code where code=%s", (m["code"],)).fetchall():
            conn.execute(
                """insert into diagnosis_mapping_version(condition_id, terminology_code_id, relationship, confidence, approval_state)
                   values (%s,%s,%s,'high',%s) on conflict do nothing""", (cond_id, tc["id"], m.get("relationship", "exact"), state))

    for s in pack.sources:
        src = conn.execute(
            """insert into source(canonical_url, publisher, title, source_type, allowlist_state, policy_reference, intended_uses)
               values (%s,%s,%s,%s,'approved',%s,%s) on conflict (canonical_url) do update set title = excluded.title returning id""",
            (s.url, s.publisher, s.title, s.source_type, s.notes, ["reference"])).fetchone()
        sv = conn.execute(
            """insert into source_version(source_id, document_identity, declared_publication_date, pipeline_state)
               values (%s,%s,null,'access_checked') returning id""", (src["id"], f"{s.title} {s.document_version or ''}".strip())).fetchone()
        cols = {op: s.rights.get(op, "unknown") for op in PERMISSION_OPS}
        cols["can_train_model"] = "denied"
        conn.execute(
            f"""insert into rights_grant(source_version_id, {','.join(cols)}, permission_evidence)
                values (%s,{','.join(['%s'] * len(cols))},%s)""", (sv["id"], *cols.values(), J(s.rights_evidence)))
        ids["sources"][s.key] = {"source_id": src["id"], "source_version_id": sv["id"], "meta": s}

    for c in pack.claims:
        svid = ids["sources"][c.source]["source_version_id"]
        row = conn.execute(
            """insert into evidence_claim(source_version_id, locator, extraction_method, paraphrase, claim_type, population,
                 recommendation_strength_as_reported, limitations) values (%s,%s,'clinician',%s,%s,%s,%s,%s) returning id""",
            (svid, J(c.locator), c.paraphrase, c.claim_type, J(c.population) if c.population else None,
             c.recommendation_strength_as_reported, c.limitations)).fetchone()
        ids["claims"][c.key] = row["id"]

    for concept in pack.concepts:
        crow = conn.execute(
            """insert into exercise_concept(preferred_name, aliases, body_region, movement_purpose) values (%s,%s,%s,%s)
               on conflict (preferred_name) do update set aliases = excluded.aliases returning id""",
            (concept.preferred_name, concept.aliases, concept.body_region, concept.movement_purpose)).fetchone()
        for v in concept.variants:
            vstate = v.approval_state.value if approved else state
            src_ref = None
            if v.source_reference:
                s = ids["sources"][v.source_reference["source"]]["meta"]
                src_ref = {"url": s.url, "publisher": s.publisher, "title": s.title, "document_version": s.document_version,
                           "locator": v.source_reference.get("locator"), "source_exercise_name": v.source_reference.get("source_exercise_name")}
            vrow = conn.execute(
                """insert into exercise_variant_version(concept_id, entity_id, version, tenant_id, name, region, joint, movement_plane,
                     target_impairment, functional_goal, starting_position, assistance, chain, load_mode, side_behavior, equipment,
                     balance_demand, setting, step_sequence, cues, common_errors, supported_modifications, source_reference,
                     approval_state, approved_by, approved_at, created_by)
                   values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id, entity_id""",
                (crow["id"], uuid.uuid4(), tenant_id, v.name, v.region, v.joint, v.movement_plane, v.target_impairment, v.functional_goal,
                 v.starting_position, v.assistance.value, v.chain if hasattr(v, "chain") else None, v.load_mode, v.side_behavior,
                 v.equipment, v.balance_demand, v.setting,
                 J([{"step": i + 1, "text": s} for i, s in enumerate(v.step_sequence)]), v.cues, v.common_errors,
                 J({"tags": v.tags, "requires_starting_position": v.requires_starting_position}), J(src_ref) if src_ref else None,
                 vstate, approver_id if approved else None, "now()" if approved else None, author_id)).fetchone()
            if v.media_state != "not_requested":
                conn.execute(
                    """insert into media_asset_version(entity_id, version, variant_version_id, media_type, media_state, approval_state)
                       values (%s,1,%s,'still_graphic',%s,%s)""", (uuid.uuid4(), vrow["id"], v.media_state, "draft"))
            ids["variants"][v.key] = {"id": vrow["id"], "entity_id": vrow["entity_id"], "tags": v.tags, "meta": v}

    for u in pack.clinical_uses:
        ustate = u.approval_state.value if approved else state
        claim_ids = [ids["claims"][k] for k in u.supporting_claims]
        dose = u.dose().model_dump(mode="json")["fields"]
        for name, f in dose.items():   # resolve claim keys to ids
            if f.get("claim_id") and f["claim_id"] in ids["claims"]:
                f["claim_id"] = str(ids["claims"][f["claim_id"]])
        urow = conn.execute(
            """insert into clinical_use_version(entity_id, version, tenant_id, variant_version_id, condition_id, presentation, indication,
                 exclusion, phase, goals, dose_envelope, supporting_claim_ids, directness, support_category, clinician_rationale,
                 approval_state, approved_by, approved_at, created_by)
               values (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (uuid.uuid4(), tenant_id, ids["variants"][u.variant]["id"], cond_id, J({**u.presentation, "session_minutes_estimate": u.session_minutes_estimate}),
             u.indication, u.exclusion, u.phase, u.goals, J(dose), claim_ids, u.directness, u.support_category, u.clinician_rationale,
             ustate, approver_id if approved else None, "now()" if approved else None, author_id)).fetchone()
        for cid in claim_ids:
            conn.execute("insert into clinical_use_evidence(clinical_use_version_id, evidence_claim_id, directness) values (%s,%s,%s) on conflict do nothing",
                         (urow["id"], cid, "direct" if u.directness == "direct" else "indirect"))
            conn.execute("insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('evidence_claim',%s,'clinical_use_version',%s,'supported_by') on conflict do nothing",
                         (cid, urow["id"]))
        conn.execute("insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('exercise_variant_version',%s,'clinical_use_version',%s,'uses_variant') on conflict do nothing",
                     (ids["variants"][u.variant]["id"], urow["id"]))
        ids["uses"][u.key] = urow["id"]

    for r in pack.rules:
        rstate = r.approval_state if approved else state
        rrow = conn.execute(
            """insert into rule_version(entity_id, version, tenant_id, name, rule_kind, expression, required_inputs, action, severity, rationale,
                 evidence_claim_ids, author_id, approver_id, approval_state, approved_at)
               values (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (uuid.uuid4(), tenant_id, r.name, r.kind, J(r.expression.model_dump(by_alias=True, exclude_none=True)), r.required_inputs,
             J({**r.action.model_dump(exclude_none=True), "key": r.key}), r.severity, r.rationale,
             [ids["claims"][k] for k in r.evidence_claim_keys], author_id, approver_id if approved else None, rstate,
             "now()" if approved else None)).fetchone()
        ids["rules"][r.key] = rrow["id"]

    for p in pack.protocols:
        pstate = p.approval_state.value if approved else state
        phases = []
        for ph in p.phases:
            phases.append({**ph.model_dump(), "items": [str(ids["uses"][k]) for k in ph.items],
                           "entry_rules": [str(ids["rules"][k]) for k in ph.entry_rules]})
        rule_ids = [ids["rules"][k] for k in p.eligibility_rules]
        prow = conn.execute(
            """insert into protocol_version(entity_id, version, tenant_id, content_pack, content_pack_version, condition_id, name,
                 population_description, author_id, provenance, setting, required_inputs, phases, monitoring, rule_version_ids,
                 recovery_horizon, approval_state, approved_by, approved_at)
               values (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (uuid.uuid4(), tenant_id, pack.pack, pack.version, cond_id, p.name, p.population_description, author_id,
             J({**p.provenance, "branch_conditions": p.branch_conditions, "reassessment": p.reassessment, "pack_label": pack.label,
                "pack_rules": [str(ids["rules"][r.key]) for r in pack.rules]}),
             p.setting, p.required_inputs, J(phases), J(p.monitoring), rule_ids, J(p.recovery_horizon) if p.recovery_horizon else None,
             pstate, approver_id if approved else None, "now()" if approved else None)).fetchone()
        for ph in p.phases:
            for i, k in enumerate(ph.items):
                conn.execute("insert into protocol_item(protocol_version_id, phase_key, clinical_use_version_id, position) values (%s,%s,%s,%s)",
                             (prow["id"], ph.key, ids["uses"][k], i))
                conn.execute("insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('clinical_use_version',%s,'protocol_version',%s,'uses_clinical_use') on conflict do nothing",
                             (ids["uses"][k], prow["id"]))
        for rid in rule_ids:
            conn.execute("insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('rule_version',%s,'protocol_version',%s,'uses_rule') on conflict do nothing",
                         (rid, prow["id"]))
        ids["protocols"][p.key] = prow["id"]

    for a in pack.applicability:
        target_id = ids["uses"][a.target] if a.target_type == "clinical_use" else ids["protocols"][a.target]
        astate = a.approval_state.value if approved else state
        arow = conn.execute(
            """insert into population_applicability_version(target_version_id, target_type, predicate_logic, relationship_type, evidence_claim_ids,
                 support_category, generalizability_limits, action_type, executable_rule_version_id, clinician_rationale, approval_state)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (target_id, a.target_type, a.predicate_logic, a.relationship_type, [ids["claims"][k] for k in a.evidence_claims], a.support_category,
             a.generalizability_limits, a.action_type, ids["rules"][a.executable_rule] if a.executable_rule else None, a.clinician_rationale, astate)).fetchone()
        for pred in a.predicates:
            conn.execute(
                """insert into population_predicate(applicability_id, dimension, operator, value, unit, min_inclusive, max_inclusive)
                   values (%s,%s,%s,%s,%s,%s,%s)""",
                (arow["id"], pred["dimension"], pred["operator"],
                 J(pred.get("value") if "value" in pred else {"min": pred.get("min"), "max": pred.get("max")}),
                 pred.get("unit"), pred.get("min_inclusive"), pred.get("max_inclusive")))
    return summary(conn, pack)


def summary(conn: psycopg.Connection, pack: ContentPack) -> dict[str, Any]:
    rows = conn.execute("select id, approval_state from protocol_version where content_pack=%s and content_pack_version=%s", (pack.pack, pack.version)).fetchall()
    return {"pack": pack.pack, "version": pack.version, "approval_state": pack.approval_state.value,
            "protocol_version_ids": [str(r["id"]) for r in rows]}


def install_all(conn: psycopg.Connection, directory: str | Path, **kw: Any) -> list[dict[str, Any]]:
    from moveai_rules.pack import load_all

    return [install_pack(conn, p, **kw) for p in load_all(directory)]
