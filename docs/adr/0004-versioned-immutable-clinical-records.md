# ADR-0004: Versioned immutable clinical records

Status: accepted · 2026-09-09

## Context

Spec §6: clinically consequential records are versioned, immutable after approval, never overwritten.

## Decision

Every *_version table has approval_state; a BEFORE UPDATE trigger rejects changes to clinical columns once approval_state is approved or published, except the explicit transitions withdrawn/superseded. Edits create a new version row with prior_version_id. dependency_edge records upstream/downstream links for impact analysis.

## Consequences

More rows, no lost history. Withdrawal propagates by walking dependency_edge.
