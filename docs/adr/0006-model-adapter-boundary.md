# ADR-0006: Model adapter boundary

Status: accepted · 2026-09-09

## Context

Spec §5/§12: LLMs extract into strict schemas and summarize rationale; they never approve, never add doses or movements, and treat source text as untrusted.

## Decision

moveai_ingestion.llm defines an ExtractionModel protocol. MockExtractionModel (deterministic, fixture-driven) is the default; AnthropicExtractionModel activates only when ANTHROPIC_API_KEY is set. Outputs are validated against the extraction schema; tool use is disabled; prompts embed the spec's extraction system instruction.

## Consequences

Tests run without network. Swapping providers is a one-file change.
