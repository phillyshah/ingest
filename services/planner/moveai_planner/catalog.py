"""Catalog access for the planner: pinned releases, protocols with their rules/uses/variants/media/applicability."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg

from moveai_rules.ast import Action, Expr, Rule

PRESCRIBABLE = ("approved", "published")


@dataclass
class LoadedRule:
    id: str
    key: str
    rule: Rule
    approval_state: str

    @property
    def executable(self) -> bool:
        return self.approval_state in PRESCRIBABLE


@dataclass
class LoadedUse:
    id: str
    row: dict[str, Any]
    variant: dict[str, Any]
    media: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    applicability: list[dict[str, Any]] = field(default_factory=list)

    @property
    def prescribable(self) -> bool:
        return self.row["approval_state"] in PRESCRIBABLE and self.variant["approval_state"] in PRESCRIBABLE


@dataclass
class LoadedProtocol:
    id: str
    row: dict[str, Any]
    condition: dict[str, Any]
    rules: dict[str, LoadedRule]          # every rule of the pack, by id
    eligibility_rule_ids: list[str]
    uses: dict[str, LoadedUse]            # by clinical_use_version id
    applicability: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]

    @property
    def prescribable(self) -> bool:
        return self.row["approval_state"] in PRESCRIBABLE

    @property
    def phases(self) -> list[dict[str, Any]]:
        return self.row["phases"]


def latest_release(conn: psycopg.Connection) -> dict | None:
    return conn.execute("select * from catalog_release order by published_at desc limit 1").fetchone()


def release_contains(conn: psycopg.Connection, release_id: Any, version_id: Any) -> bool:
    return bool(conn.execute("select 1 from catalog_release_item where release_id=%s and version_id=%s", (release_id, version_id)).fetchone())


def _rule(row: dict) -> LoadedRule:
    action = dict(row["action"])
    key = action.pop("key", row["name"])
    return LoadedRule(id=str(row["id"]), key=key, approval_state=row["approval_state"],
                      rule=Rule(key=key, name=row["name"], kind=row["rule_kind"], expression=Expr.model_validate(row["expression"]),
                                action=Action.model_validate(action), severity=row["severity"], rationale=row["rationale"],
                                approval_state=row["approval_state"]))


def load_protocols(conn: psycopg.Connection, condition_ids: list[Any], *, release_id: Any = None,
                   include_unsigned: bool = True) -> list[LoadedProtocol]:
    """Prescribable protocols must be in the pinned release when one is given. Unsigned placeholders are loaded only
    for previews (never prescribable)."""
    if not condition_ids:
        return []
    rows = conn.execute(
        """select p.*, c.internal_code, c.preferred_name as condition_name from protocol_version p join condition c on c.id = p.condition_id
            where p.condition_id = any(%s) and p.approval_state not in ('rejected','withdrawn','superseded','invalidated')
            order by p.created_at""", (condition_ids,)).fetchall()
    out: list[LoadedProtocol] = []
    for p in rows:
        if p["approval_state"] in PRESCRIBABLE and release_id is not None and not release_contains(conn, release_id, p["id"]):
            continue
        if p["approval_state"] not in PRESCRIBABLE and not include_unsigned:
            continue
        rule_ids = [str(r) for r in (p["provenance"] or {}).get("pack_rules", [])] or [str(r) for r in p["rule_version_ids"]]
        rules = {str(r["id"]): _rule(r) for r in conn.execute("select * from rule_version where id = any(%s::uuid[])", (rule_ids,)).fetchall()}
        uses: dict[str, LoadedUse] = {}
        for ph in p["phases"]:
            for uid in ph["items"]:
                if uid in uses:
                    continue
                u = conn.execute("select * from clinical_use_version where id=%s", (uid,)).fetchone()
                if not u:
                    continue
                v = conn.execute("select * from exercise_variant_version where id=%s", (u["variant_version_id"],)).fetchone()
                media = conn.execute("select m.*, g.can_display_to_patient, g.can_display_to_clinician, g.expires_at, g.revoked_at from media_asset_version m "
                                     "left join rights_grant g on g.id = m.rights_grant_id where m.variant_version_id=%s", (v["id"],)).fetchall()
                claims = conn.execute("select ec.*, sv.final_url, s.canonical_url, s.publisher, s.title as source_title, sv.document_identity "
                                      "from evidence_claim ec join source_version sv on sv.id=ec.source_version_id join source s on s.id=sv.source_id "
                                      "where ec.id = any(%s::uuid[])", ([str(c) for c in u["supporting_claim_ids"]],)).fetchall()
                app = conn.execute("select a.*, (select json_agg(pp) from population_predicate pp where pp.applicability_id=a.id) as predicates "
                                   "from population_applicability_version a where a.target_type='clinical_use' and a.target_version_id=%s", (uid,)).fetchall()
                uses[str(uid)] = LoadedUse(id=str(uid), row=u, variant=v, media=media, claims=claims, applicability=app)
        app_p = conn.execute("select a.*, (select json_agg(pp) from population_predicate pp where pp.applicability_id=a.id) as predicates "
                             "from population_applicability_version a where a.target_type='protocol' and a.target_version_id=%s", (p["id"],)).fetchall()
        refs = []
        for u in uses.values():
            for c in u.claims:
                refs.append({"url": c["canonical_url"], "publisher": c["publisher"], "title": c["source_title"], "document_version": c["document_identity"],
                             "locator": c["locator"], "evidence_claim_id": str(c["id"])})
        out.append(LoadedProtocol(id=str(p["id"]), row=p, condition={"id": p["condition_id"], "code": p["internal_code"], "name": p["condition_name"]},
                                  rules=rules, eligibility_rule_ids=[str(r) for r in p["rule_version_ids"]], uses=uses, applicability=app_p, source_refs=refs))
    return out


def all_conditions(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute("select * from condition order by preferred_name").fetchall()
