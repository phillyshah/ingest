# ADR-0002: Postgres job queue instead of Redis

Status: accepted · 2026-09-09

## Context

Spec §5/§22 require a durable queue with checkpoints and idempotent at-least-once delivery, and ask that we not add services the VPS cannot afford.

## Decision

Jobs live in ingestion_job. Workers claim with SELECT ... FOR UPDATE SKIP LOCKED, hold a lease with heartbeat, retry with bounded exponential backoff and jitter, and dead-letter after max attempts. Events go through an outbox table in the same transaction as state changes.

## Consequences

One less service to run. Throughput is bounded by Postgres, which is fine at MVP concurrency (start at 1-2 workers). A Redis/NATS queue can replace the claim function later without changing job semantics.
