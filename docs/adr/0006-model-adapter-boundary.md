# ADR-0006: Model adapter boundary

Status: accepted · 2026-09-09

## Context

Spec §5/§12: LLMs extract into strict schemas and summarize rationale; they never approve, never add doses or movements, and treat source text as untrusted.

## Decision

moveai_ingestion.llm defines an ExtractionModel protocol. MockExtractionModel (deterministic, fixture-driven) is the default. AnthropicExtractionModel and OpenRouterExtractionModel implement the same protocol; the latter reaches open-weight models and takes its cost from OpenRouter's reported per-call figure, since per-model rates there span orders of magnitude. Naming a provider without its key raises rather than falling back to the mock, whose fixture output is indistinguishable from a real extraction. Outputs are validated against the extraction schema; tool use is disabled; prompts embed the spec's extraction system instruction. The schema gate, not the model, is what rejects injected fields — which matters more the weaker the model.

## Consequences

Tests run without network. Swapping providers is a one-file change.
