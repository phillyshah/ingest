# ADR-0001: Supabase Postgres on owner VPS

Status: accepted · 2026-09-09

## Context

Spec §22 fixes Supabase PostgreSQL as the authoritative store and the owner's VPS as host behind an existing reverse proxy at ingest.phillyshah.com.

## Decision

Plain PostgreSQL 16 SQL migrations compatible with the Supabase CLI. No Supabase-only features in core paths; Supabase Auth and Storage are integrated behind interfaces (moveai_api.auth, moveai_ingestion.storage). Local dev uses a throwaway Postgres 16 cluster (scripts/dev_pg.sh).

## Consequences

Migrations must be additive and reviewable. RLS is enabled on tenant-scoped tables and driven by the app.tenant_id GUC so both the API and Supabase clients see identical isolation.
