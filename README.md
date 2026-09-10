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

## Status (sprint 1)

Working: schema + DB-enforced invariants, ingestion pipeline (HTML, text PDF, OCR stub) with rights gates and
provenance, content-pack installer, deterministic planner (`needs_assessment` / `blocked_for_clinical_review` /
`draft_ready`) with population segmentation, PT approval with signed revisions, withdrawal propagation, immutable
catalog releases, `/v1` API with campaign board endpoints and SSE, mock MoveAI adapter, reviewer app (Kanban,
campaign detail, extraction review, catalog, plan options), 126 Python tests, 6 UI unit tests, a Playwright smoke,
and `make demo`. Deployment files target the owner's VPS + Supabase (spec §22) but nothing has been deployed.

Also working: Supabase deployment hardening. Migration 0010 removes all `anon`/`authenticated` privileges and
enables row-level security on every table, because Supabase publishes the `public` schema through PostgREST
(ADR-0008). `AUTH_MODE=supabase` verifies real Supabase Auth tokens against the project JWKS and requires an
invitation. See `docs/runbooks/supabase-deployment.md`.

Not yet: a real OCR engine, live crawling of allowlisted domains beyond fixtures, signed clinical content (all
three clinical packs are `unsigned_placeholder`).

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

### Reviewer app and end-to-end smoke

```bash
pnpm -C apps/reviewer install
make api                              # terminal 1
make ui                               # terminal 2 -> http://127.0.0.1:5173 (proxies /api/v1 to the API)
make seed                             # prints user IDs; also writes .demo-out/e2e.env
PW_CHROMIUM=/path/to/chromium pnpm -C apps/reviewer test:e2e   # or let Playwright download its browser
```

Sign in with a user ID from `make seed` and the role you want to exercise. The board, detail tabs, review screen,
and plan options are all live against the API.

### Deployment

Two runbooks, both written to be followed step by step:

- `docs/runbooks/vps-deployment.md` — the full path from an empty server to a live site, for a non-technical
  operator. Every step is a click or a single paste.
- `docs/runbooks/supabase-deployment.md` — the database specifics, connection choices, rollback and rotation.

Deployment runs through GitHub Actions rather than a laptop, so credentials live only in GitHub's encrypted
secret store: **Migrate Supabase** applies the schema and proves the exposure boundary, and **Verify deployment**
checks the live site from outside, including that the owner's other sites still work.

`deploy/inspect.sh` reads a server and changes nothing. `deploy/install.sh` is a dry run unless given `--apply`,
adds only one new virtual host, validates the web-server config before reloading, and rolls its own change back if
validation fails. `infra/` holds the env template, Dockerfiles, compose file, proxy snippets and a systemd unit.

Auth in development is a header shim (`X-Role`, `X-Tenant-Id`, `X-User-Id`). Supabase Auth replaces the
shim behind the same interface (`moveai_api.auth`). Production target: `https://ingest.phillyshah.com`.
