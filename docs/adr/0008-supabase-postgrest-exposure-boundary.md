# ADR-0008: Supabase PostgREST exposure boundary

Status: accepted · 2026-09-10

## Context

The engine was developed against plain PostgreSQL, where only the API connects and it connects as a role we
control. Table-level grants were therefore sufficient, and row-level security was applied only to the patient
store, where cross-tenant isolation is the requirement.

Supabase is a different threat model. PostgREST publishes the `public` schema over HTTPS, and the project's
default privileges grant the `anon` and `authenticated` roles access to tables in it. Row-level security is the
only thing standing between a table and the public internet. At the point the owner provisioned the `ingest`
project, 41 of 44 engine tables had no RLS.

Migrating as-is would have let anyone holding the anon key — public by design, shipped in the browser bundle —
read and write `rights_grant`, `source`, `evidence_claim`, `protocol_version`, `rule_version`, `review_event` and
`audit_event`. The immutability triggers still protect approved rows, but records could be inserted, pending ones
approved, or published ones withdrawn. That is a clinical-integrity failure, not merely a confidentiality one.

Spec §22C requires tenant-scoped RLS and explicitly authorized API operations, and warns that a schema name alone
is never a security boundary.

## Decision

Deny at two independent layers, in `db/migrations/0010_supabase_exposure_hardening.sql`:

1. **Privileges.** `anon` and `authenticated` hold no grant on any engine table, sequence or function, and no
   usage on schema `public`. Default privileges are reset so future objects are not granted either.
2. **Row-level security.** Enabled on every engine table. Catalog tables get one permissive policy for the backend
   role `moveai_app`; the patient tables keep the forced tenant policies from migration 0005. Roles with no policy
   match nothing.

RLS is deliberately *not* forced on catalog tables, so the owner role can still run migrations and maintenance.
Every reference to `anon` and `authenticated` is guarded by a `pg_roles` existence check, so the migration is a
no-op on local PostgreSQL and in CI where those roles do not exist.

Because the guards make the migration silent off Supabase, correctness is proven against the real project by
`scripts/verify_supabase.py`, not by the local test suite alone.

We rejected relocating the tables into an unexposed schema. It is stronger isolation, but a much larger migration
to review, and the spec is explicit that a schema name is not by itself a boundary. It remains available later as
defense in depth.

## Consequences

- The anon key can reach nothing. The browser talks to the API, which holds the only database credentials.
- Any new table needs a policy, or it is unreachable by everyone including the backend. A test enforces this.
- Supabase client libraries cannot be used against these tables directly. That is intended: all access goes
  through the versioned API where role checks, audit and idempotency live.
