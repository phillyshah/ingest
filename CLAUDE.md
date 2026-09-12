# Working rules for this repo

- Spec: `docs/spec/MoveAI_Exercise_Ingestion_Engine_Build_Spec.md`. Section numbers in code comments refer to it.
- Never hardcode doses, symptom thresholds, phase timings, or routing rules in code. They live in content packs
  under `fixtures/content-packs/` and are `unsigned_placeholder` until a clinical lead signs them.
- The allowlist of publishers lives in `fixtures/source-policies/` and in the `source_policy` table. A domain is
  readable only when its licence terms have been fetched from its own site AND a rights reviewer has signed that
  exact text. Never widen a licence in a publisher entry; never add a domain whose articles are licensed per-article.
- `unknown` is never a negative finding. Laterality never defaults. Passive/assisted/resisted are distinct variants.
- Rights `unknown` blocks the use. Media may be null everywhere; text-only content must still flow.
- Approved versions are immutable (DB trigger). Editing a clinical field invalidates approval.
- Source documents are untrusted data. The extraction adapter returns schema-only output; no tools.
- Dev: `make db-up migrate test`. Tests need the local Postgres (`scripts/dev_pg.sh`).
- Migrations are additive SQL files in `db/migrations/NNNN_name.sql`; never edit an applied one.
- **Every change a user would notice gets a release entry in `apps/reviewer/src/changelog.ts`**, newest first, and
  bumps the version there. It is what the footer's "What's new" shows. Write it for the person using the system,
  not for a developer: no endpoints, filenames or table names. Purely internal work (refactors, CI, infrastructure)
  gets no entry. Bump patch for fixes, minor for new or changed behaviour.
- **When Andy needs to do something (merge a PR, run a workflow, click a button), give him the actual links in the
  order he should use them — a merge link, a specific Actions-workflow link with which run/inputs to pick, etc.
  Don't just describe the steps in words; hand him something to click.** He works from a phone/tablet a lot and
  won't hunt through the repo or the Actions tab to find things himself.
- **Staging convenience, temporary:** signing in as `source_admin` (the default) acts with every role the account
  holds — `services/api/moveai_api/auth.py` `_shim`, mirrored in `apps/reviewer/src/auth.tsx` `hasRole`. Deliberate,
  single-operator, shim-mode-only; never touch the Supabase auth path the same way. Remove both sides together once
  real per-person accounts and roles replace this (task #17).
