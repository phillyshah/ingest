# MoveAI Exercise Ingestion and Plan-Drafting Engine

Independently deployable service that discovers rehabilitation resources, extracts traceable clinical
knowledge, normalizes exercise variants, manages rights, and helps a physical therapist draft and approve
individualized recovery plans. Built to the MoveAI build spec v1.4 (see `docs/spec/`).

**This code never invents clinical content.** Doses, thresholds, phase criteria, and routing rules come from
signed clinical content packs. The packs shipped here are `unsigned_placeholder` and cannot be published.

## Layout

| Path | What |
| --- | --- |
| `apps/reviewer/` | Admin Kanban + PT review UI (Vite/React/TS) |
| `services/api/` | FastAPI `/v1` API (served publicly under `/api/v1/`) |
| `services/ingestion/` | Postgres-backed job queue and pipeline stages |
| `services/planner/` | Deterministic routing, eligibility, ranking, validation, approval |
| `packages/contracts/` | Pydantic models, enums, OpenAPI export |
| `packages/clinical_rules/` | Typed rule language, evaluator, BMI classifier, content-pack loader |
| `adapters/moveai/` | Mock MoveAI client (approved-plan retrieval + `/changes`) |
| `db/migrations/` | Additive SQL migrations (Supabase-compatible) + RLS/invariant tests |
| `fixtures/` | Permitted sources, content packs, synthetic cases |
| `infra/` | compose, Caddy snippet, systemd units, env template |
| `docs/adr/`, `docs/runbooks/` | Decisions and operations |

## Quick start

```bash
make db-up        # local Postgres 16 on 127.0.0.1:55432 (dev only)
make migrate
make seed
make test
make demo         # end-to-end acceptance demonstration (text-only, no media)
make api          # http://127.0.0.1:8000/v1  (docs at /v1/docs)
pnpm -C apps/reviewer install && make ui
```

Auth in development is a header shim (`X-Role`, `X-Tenant-Id`, `X-User-Id`). Supabase Auth replaces the
shim behind the same interface (`moveai_api.auth`). Production target: `https://ingest.phillyshah.com`.
