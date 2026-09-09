# ADR-0007: Code never supplies clinical content

Status: accepted · 2026-09-09

## Context

Spec §1/§12/§14: engineers implement representation and checks; the clinical lead supplies and signs doses, thresholds, and routing.

## Decision

Content packs (fixtures/content-packs/*.yaml) carry all clinical values with provenance and approval_state. Shipped packs are unsigned_placeholder: doses are null with a reason, routing rules are require_field/block_for_review only. The publisher refuses unsigned packs. A separate demo_synthetic pack, labeled non-clinical, exists only so the draft_ready -> approve -> adapter path can be exercised.

## Consequences

The planner can be demonstrated end-to-end while clinical acceptance remains a separate gate.
