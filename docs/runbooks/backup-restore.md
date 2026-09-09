# Runbook: Backup restore

## Symptoms
Data loss or corruption in the Supabase project.

## Diagnosis
Confirm the project's backup/PITR capability (do not assume the plan includes it, spec §22C). Identify the last good point from audit_event timestamps.

## Action
Restore to a scratch project/database first; verify schema_migration, catalog_release manifests (hashes must match), and plan_version signatures (PLAN_SIGNING_SECRET); then switch DATABASE_URL and restart api/worker/scheduler. Re-run the outbox dispatcher; consumers reconcile via GET /changes.

## Never
Restore over production without verification or rotate PLAN_SIGNING_SECRET without re-signing.
