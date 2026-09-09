# Runbook: Rollback

## Symptoms
A deployment regresses behaviour.

## Diagnosis
Compare the running image tag with the previous release; check GET /v1/diagnostics for migration names.

## Action
Migrations are additive: rolling the application back to the previous image is safe when the new migrations only add objects (all shipped migrations do). `docker compose pull <previous tag> && docker compose up -d` (or systemctl restart moveai-ingest after changing the tag). Do not drop new tables.

## Never
Run destructive migrations during rollback or reset the database.
