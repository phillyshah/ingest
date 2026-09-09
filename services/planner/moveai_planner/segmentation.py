"""Population segmentation (spec §19): derive observations from confirmed data and a pinned classification version,
persist them in the patient store, and explain whether each characteristic changed the plan."""

from __future__ import annotations

from typing import Any

import psycopg
from moveai_contracts.enums import FieldStatus
from moveai_contracts.intake import Intake
from moveai_contracts.plan import SegmentExplanation
from moveai_db import J
from moveai_rules.bmi import CDC_ADULT_BMI_V2024, classify_bmi, compute_bmi, to_kg, to_meters


def ensure_bmi_definition(conn: psycopg.Connection) -> dict:
    d = CDC_ADULT_BMI_V2024
    row = conn.execute(
        "select * from segmentation_definition_version where dimension='bmi' and authority=%s and authority_version=%s",
        (d["authority"], d["authority_version"]),
    ).fetchone()
    if row:
        return row
    return conn.execute(
        """insert into segmentation_definition_version(dimension, authority, authority_version, population_scope, boundaries, derivation, source_url, approval_state)
           values ('bmi',%s,%s,%s,%s,'weight_kg / height_m^2, unrounded',%s,'approved') returning *""",
        (d["authority"], d["authority_version"], d["population_scope"], J(d["boundaries"]), d["source_url"]),
    ).fetchone()


def derive_observations(conn: psycopg.Connection, tenant_id: Any, snapshot_id: Any, intake: Intake) -> list[dict[str, Any]]:
    """Returns observation dicts (also persisted). Every dimension keeps unknown/not_assessed/not_applicable distinct."""
    obs: list[dict[str, Any]] = []
    bmi_def = ensure_bmi_definition(conn)

    def add(
        dimension: str,
        f,
        *,
        value=None,
        unit=None,
        status=None,
        classified=None,
        classification_version_id=None,
        method=None,
    ):
        o = {
            "dimension": dimension,
            "value": value if value is not None else (f.value if f else None),
            "unit": unit or (f.unit if f else None),
            "status": status or (f.status if f else FieldStatus.unknown),
            "provenance": (f.provenance if f else "derived"),
            "method": method or (f.provenance if f else "derived"),
            "classified_value": classified,
            "classification_version_id": classification_version_id,
            "reported_text": f.reported_text if f else None,
        }
        obs.append(o)
        conn.execute(
            """insert into case_observation(tenant_id, case_snapshot_id, dimension, value, unit, status, measured_at, method, provenance, classification_version_id, classified_value)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                tenant_id,
                snapshot_id,
                dimension,
                J(o["value"]),
                o["unit"],
                o["status"],
                f.measured_at if f else None,
                o["method"],
                o["provenance"],
                classification_version_id,
                classified,
            ),
        )

    for dim in (
        "age",
        "sex",
        "gender",
        "height",
        "weight",
        "comorbidities",
        "functional_limitations",
        "equipment",
        "language",
    ):
        add(dim, intake.get(dim))
    age = intake.value("age")
    h, w = intake.get("height"), intake.get("weight")
    bmi_f = intake.get("bmi")
    bmi_val: float | None = None
    if h.is_known and w.is_known:
        try:
            bmi_val = compute_bmi(to_meters(float(h.value), h.unit or "m"), to_kg(float(w.value), w.unit or "kg"))
        except ValueError:
            bmi_val = None
    elif bmi_f.is_known:
        bmi_val = float(bmi_f.value)
    if bmi_val is not None:
        cls = classify_bmi(bmi_val, float(age) if age is not None else None)
        add(
            "bmi",
            None,
            value=round(bmi_val, 4),
            unit="kg/m2",
            status=FieldStatus.known,
            classified=cls.klass or f"not_classified: {cls.reason}",
            classification_version_id=bmi_def["id"],
            method="derived",
        )
    else:
        add(
            "bmi",
            bmi_f,
            status=bmi_f.status if bmi_f.status != FieldStatus.known else FieldStatus.unknown,
            classified=None,
            method="not_measured",
        )
    return obs


def _pred_matches(pred: dict[str, Any], observations: dict[str, dict[str, Any]]) -> bool | None:
    o = observations.get(pred["dimension"])
    if not o or o["status"] != FieldStatus.known or o["value"] is None:
        return None
    try:
        v = float(o["value"])
    except (TypeError, ValueError):
        v = o["value"]
    val = pred.get("value")
    op = pred["operator"]
    if op == "between" and isinstance(val, dict):
        lo, hi = val.get("min"), val.get("max")
        lo_ok = lo is None or (v >= lo if pred.get("min_inclusive", True) else v > lo)
        hi_ok = hi is None or (v <= hi if pred.get("max_inclusive", True) else v < hi)
        return lo_ok and hi_ok
    try:
        return {
            "eq": v == val,
            "ne": v != val,
            "lt": v < val,
            "lte": v <= val,
            "gt": v > val,
            "gte": v >= val,
            "in": v in (val or []),
            "not_in": v not in (val or []),
        }[op]
    except TypeError:
        return None


def explain_segments(
    observations: list[dict[str, Any]], applicability: list[dict[str, Any]], bmi_def_version: str
) -> list[SegmentExplanation]:
    """One explanation per relevant dimension. Only approved executable rules change the plan; everything else is context."""
    by_dim = {o["dimension"]: o for o in observations}
    out: list[SegmentExplanation] = []
    for dim in ("age", "sex", "gender", "bmi", "comorbidities", "functional_limitations"):
        o = by_dim.get(dim)
        if not o:
            continue
        observed = None
        if o["status"] == FieldStatus.known and o["value"] is not None:
            observed = f"{o['value']}{' ' + o['unit'] if o.get('unit') else ''}"
        elif o.get("reported_text"):
            observed = f"reported as {o['reported_text']!r} (not measured)"
        else:
            observed = f"{o['status']}"
        exp = SegmentExplanation(
            dimension=dim,
            observed=observed,
            classification=o.get("classified_value"),
            classification_version=bmi_def_version if dim == "bmi" and o.get("classified_value") else None,
        )
        relevant = [a for a in applicability if any(p["dimension"] == dim for p in (a["predicates"] or []))]
        if not relevant:
            exp.evidence_gap = "no population-applicability record for this dimension on the candidate pathways"
        for a in relevant:
            results = [_pred_matches(p, by_dim) for p in (a["predicates"] or [])]
            matched = all(r is True for r in results) if a["predicate_logic"] == "and" else any(r is True for r in results)
            if any(r is None for r in results):
                exp.effect = f"applicability record ({a['relationship_type']}) could not be evaluated: observation missing"
            elif (
                matched
                and a["action_type"] in ("adapt", "monitor", "exclude")
                and a["approval_state"] in ("approved", "published")
                and a["executable_rule_version_id"]
            ):
                exp.effect = f"approved executable rule applies ({a['action_type']})"
                exp.rule_version_id = str(a["executable_rule_version_id"])
            elif matched:
                exp.effect = f"matches {a['relationship_type']} tag; action={a['action_type']}; recorded for context; no supported modification applied"
                if a.get("generalizability_limits"):
                    exp.evidence_gap = a["generalizability_limits"]
            else:
                exp.effect = f"outside {a['relationship_type']} tag; not an exclusion (study exclusions are not contraindications)"
        out.append(exp)
    return out
