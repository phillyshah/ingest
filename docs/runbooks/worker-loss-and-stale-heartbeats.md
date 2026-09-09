# Runbook: Worker loss and stale heartbeats

## Symptoms
Campaign card shows 'heartbeat expired'; jobs stuck in running.

## Diagnosis
`select * from ingestion_job where state='running' and leased_until < now()`.

## Action
The worker's reclaim loop returns expired leases to queued (idempotent stages prevent duplicate records). If no worker is alive, restart the worker service; the UI shows stale status rather than claiming progress.

## Never
Manually flip job states without checking leases.
