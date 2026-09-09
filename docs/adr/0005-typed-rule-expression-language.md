# ADR-0005: Typed rule expression language

Status: accepted · 2026-09-09

## Context

Spec §3D/§8: rules must be executable, explainable, and unable to express unsupported logic.

## Decision

Rules are a small JSON AST (packages/clinical_rules/ast.py): field predicates with tri-state unknown semantics, and/or/not, boundary-inclusive ranges, and a fixed action vocabulary. The evaluator returns a trace. Anything outside the grammar fails validation and cannot be saved as executable.

## Consequences

No arbitrary code in rules. New operators require a schema version bump.
