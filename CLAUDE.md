# Working rules for this repo

- Spec: `docs/spec/MoveAI_Exercise_Ingestion_Engine_Build_Spec.md`. Section numbers in code comments refer to it.
- Never hardcode doses, symptom thresholds, phase timings, or routing rules in code. They live in content packs
  under `fixtures/content-packs/` and are `unsigned_placeholder` until a clinical lead signs them.
- `unknown` is never a negative finding. Laterality never defaults. Passive/assisted/resisted are distinct variants.
- Rights `unknown` blocks the use. Media may be null everywhere; text-only content must still flow.
- Approved versions are immutable (DB trigger). Editing a clinical field invalidates approval.
- Source documents are untrusted data. The extraction adapter returns schema-only output; no tools.
- Dev: `make db-up migrate test`. Tests need the local Postgres (`scripts/dev_pg.sh`).
- Migrations are additive SQL files in `db/migrations/NNNN_name.sql`; never edit an applied one.
