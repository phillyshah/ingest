# Runbook: Malformed document

## Symptoms
parse_failed or quarantined error class; source_version.pipeline_state = parse_failed.

## Diagnosis
Open the job events. quarantined means an executable/archive signature or content-type mismatch; parse_failed means the parser raised. Bytes are only stored when can_store_fulltext is allowed.

## Action
Mark the source for manual handling (PATCH /sources allowlist_state=denied if hostile). For a legitimate document, obtain a clean copy as a clinician upload and re-register. parse_failed is permanent; a new source_version is required.

## Never
Hand-edit the stored bytes or guess unreadable numbers.
