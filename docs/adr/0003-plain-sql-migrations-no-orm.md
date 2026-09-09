# ADR-0003: Plain SQL migrations, no ORM

Status: accepted · 2026-09-09

## Context

Critical invariants (immutability after approval, append-only audit, tenant FKs, RLS) are database concerns; an ORM hides them.

## Decision

Schema is defined in db/migrations/*.sql, applied in order by scripts/migrate.py and recorded in schema_migration. Python uses psycopg 3 with typed row factories and pydantic models from packages/contracts.

## Consequences

Contracts and schema are maintained in parallel; a test asserts every contract enum matches its SQL enum.
