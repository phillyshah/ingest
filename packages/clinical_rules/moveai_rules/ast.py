"""Typed rule expression language (ADR-0005, spec §3D/§8).

Grammar (JSON):
  {"op": "and"|"or", "args": [Expr, ...]}
  {"op": "not", "arg": Expr}
  {"op": "known", "field": F}                          -> TRUE iff intake field status is known
  {"op": "status", "field": F, "is": FieldStatus}
  {"op": "eq"|"ne"|"lt"|"lte"|"gt"|"gte"|"in"|"contains", "field": F, "value": V, "unit"?: U}
  {"op": "between", "field": F, "min": N, "max": N, "min_inclusive"?: bool, "max_inclusive"?: bool, "unit"?: U}
  {"op": "true"} | {"op": "false"}

Three-valued semantics: a comparison on a field whose status is unknown/not_assessed evaluates to UNKNOWN; a field
marked not_applicable (the attribute cannot exist for this case, e.g. "prior session response" on a first visit)
evaluates comparisons to FALSE. and/or/not follow Kleene logic. `known`/`status` are always definite. Anything outside this grammar fails validation and cannot be saved as executable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from moveai_contracts.enums import FieldStatus
from moveai_contracts.intake import INTAKE_FIELDS, Intake
from pydantic import BaseModel, Field, ValidationError, model_validator

SCHEMA_VERSION = 1


class Tri(Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def _and(vals: list[Tri]) -> Tri:
    if any(v is Tri.FALSE for v in vals):
        return Tri.FALSE
    if all(v is Tri.TRUE for v in vals):
        return Tri.TRUE
    return Tri.UNKNOWN


def _or(vals: list[Tri]) -> Tri:
    if any(v is Tri.TRUE for v in vals):
        return Tri.TRUE
    if all(v is Tri.FALSE for v in vals):
        return Tri.FALSE
    return Tri.UNKNOWN


def _not(v: Tri) -> Tri:
    return {Tri.TRUE: Tri.FALSE, Tri.FALSE: Tri.TRUE, Tri.UNKNOWN: Tri.UNKNOWN}[v]


COMPARISON_OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains")


class Expr(BaseModel):
    op: Literal[
        "and",
        "or",
        "not",
        "known",
        "status",
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "in",
        "contains",
        "between",
        "true",
        "false",
    ]
    args: list[Expr] | None = None
    arg: Expr | None = None
    field: str | None = None
    is_: FieldStatus | None = Field(default=None, alias="is")
    value: Any = None
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def _shape(self) -> Expr:
        op = self.op
        if op in ("and", "or"):
            if not self.args:
                raise ValueError(f"{op} needs args")
        elif op == "not":
            if self.arg is None:
                raise ValueError("not needs arg")
        elif op in ("true", "false"):
            pass
        else:
            if not self.field:
                raise ValueError(f"{op} needs field")
            if self.field not in INTAKE_FIELDS:
                raise ValueError(f"unknown intake field {self.field!r}")
            if op == "status" and self.is_ is None:
                raise ValueError("status needs 'is'")
            if op == "between" and (self.min is None and self.max is None):
                raise ValueError("between needs min or max")
            if op in COMPARISON_OPS and self.value is None:
                raise ValueError(f"{op} needs value")
        return self

    def fields(self) -> set[str]:
        out: set[str] = set()
        if self.field:
            out.add(self.field)
        for a in self.args or []:
            out |= a.fields()
        if self.arg:
            out |= self.arg.fields()
        return out


class Trace(BaseModel):
    op: str
    field: str | None = None
    result: Tri
    detail: str | None = None
    children: list[Trace] = Field(default_factory=list)


class Evaluation(BaseModel):
    result: Tri
    unknown_fields: list[str] = Field(default_factory=list)
    trace: Trace


def parse_expr(raw: dict[str, Any]) -> Expr:
    try:
        return Expr.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"unsupported rule expression: {e.errors()[0]['msg']}") from e


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate(expr: Expr, intake: Intake) -> Evaluation:
    unknown: list[str] = []

    def ev(e: Expr) -> Trace:
        if e.op in ("and", "or"):
            kids = [ev(a) for a in e.args or []]
            r = (_and if e.op == "and" else _or)([k.result for k in kids])
            return Trace(op=e.op, result=r, children=kids)
        if e.op == "not":
            k = ev(e.arg)  # type: ignore[arg-type]
            return Trace(op="not", result=_not(k.result), children=[k])
        if e.op == "true":
            return Trace(op="true", result=Tri.TRUE)
        if e.op == "false":
            return Trace(op="false", result=Tri.FALSE)
        f = intake.get(e.field or "")
        if e.op == "known":
            return Trace(op="known", field=e.field, result=Tri.TRUE if f.is_known else Tri.FALSE)
        if e.op == "status":
            return Trace(
                op="status",
                field=e.field,
                result=Tri.TRUE if f.status == e.is_ else Tri.FALSE,
                detail=f"status={f.status}",
            )
        if f.status == FieldStatus.not_applicable:
            return Trace(op=e.op, field=e.field, result=Tri.FALSE, detail="not_applicable: attribute cannot be present")
        if not f.is_known:
            unknown.append(e.field or "")
            return Trace(op=e.op, field=e.field, result=Tri.UNKNOWN, detail=f"field status {f.status}")
        if e.unit and f.unit and e.unit != f.unit:
            unknown.append(e.field or "")
            return Trace(op=e.op, field=e.field, result=Tri.UNKNOWN, detail=f"unit mismatch {f.unit}!={e.unit}")
        v = f.value
        if e.op == "eq":
            r = v == e.value
        elif e.op == "ne":
            r = v != e.value
        elif e.op == "in":
            r = v in (e.value or [])
        elif e.op == "contains":
            r = e.value in (v if isinstance(v, list | str) else [])
        elif e.op in ("lt", "lte", "gt", "gte"):
            n, t = _num(v), _num(e.value)
            if n is None or t is None:
                unknown.append(e.field or "")
                return Trace(op=e.op, field=e.field, result=Tri.UNKNOWN, detail="non-numeric")
            r = {"lt": n < t, "lte": n <= t, "gt": n > t, "gte": n >= t}[e.op]
        elif e.op == "between":
            n = _num(v)
            if n is None:
                unknown.append(e.field or "")
                return Trace(op=e.op, field=e.field, result=Tri.UNKNOWN, detail="non-numeric")
            lo_ok = True if e.min is None else (n >= e.min if e.min_inclusive else n > e.min)
            hi_ok = True if e.max is None else (n <= e.max if e.max_inclusive else n < e.max)
            r = lo_ok and hi_ok
        else:  # pragma: no cover
            raise ValueError(e.op)
        return Trace(op=e.op, field=e.field, result=Tri.TRUE if r else Tri.FALSE, detail=f"value={v!r}")

    t = ev(expr)
    return Evaluation(result=t.result, unknown_fields=sorted(set(unknown)), trace=t)


# ---- actions ----
ACTION_TYPES = ("require_field", "block_for_review", "exclude_variant", "inform", "rank", "monitor", "adapt")
EXECUTABLE_ONLY_WHEN_APPROVED = ("exclude_variant", "adapt", "monitor")


class Action(BaseModel):
    type: Literal["require_field", "block_for_review", "exclude_variant", "inform", "rank", "monitor", "adapt"]
    field: str | None = None
    fields: list[str] = Field(default_factory=list)
    reason: str | None = None
    message: str | None = None
    variant_keys: list[str] = Field(default_factory=list)
    assistance: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    adjustment: float | None = None
    substitute_variant_key: str | None = None
    routing_message: str | None = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _shape(self) -> Action:
        if self.type == "require_field" and not (self.field or self.fields):
            raise ValueError("require_field needs field(s)")
        if self.type == "block_for_review" and not self.message:
            raise ValueError("block_for_review needs message")
        if self.type == "exclude_variant" and not (self.variant_keys or self.assistance or self.tags):
            raise ValueError("exclude_variant needs a target")
        if self.type == "rank" and self.adjustment is None:
            raise ValueError("rank needs adjustment")
        if self.type == "adapt" and not self.substitute_variant_key:
            raise ValueError("adapt needs substitute_variant_key")
        return self


class Rule(BaseModel):
    key: str
    name: str
    kind: Literal[
        "concern_screen",
        "restriction",
        "eligibility",
        "progression",
        "regression",
        "symptom_limit",
        "next_day_response",
        "population_modifier",
        "required_input",
    ]
    expression: Expr
    action: Action
    severity: Literal["info", "warning", "block"] = "info"
    rationale: str | None = None
    evidence_claim_keys: list[str] = Field(default_factory=list)
    approval_state: str = "unsigned_placeholder"

    @property
    def required_inputs(self) -> list[str]:
        return sorted(self.expression.fields())
