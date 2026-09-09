# Runbook: Source outage

## Symptoms
Fetch jobs for one domain fail with fetch_failed; campaign card shows retries; no other domains affected.

## Diagnosis
`GET /v1/ingestion-jobs?state=failed` and the Sources & Jobs tab: error_class fetch_failed with HTTP/DNS detail. Check the domain outside the worker.

## Action
Jobs retry with bounded backoff automatically. If the outage is prolonged: pause the campaign (control stays visible), or let jobs dead-letter and use Retry failed later. One failed URL never blocks other sources (spec §21C).

## Never
Bypass the allowlist, fetch through a different domain, or disable SSRF checks.
