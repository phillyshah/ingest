# Runbook: Cost spike

## Symptoms
Dashboard spend_today above expectation; campaign spend approaching cap.

## Diagnosis
Activity & Costs tab: budget ledger and cost by stage. reserve_budget refuses dispatch beyond the cap; in-flight bounded work may still settle.

## Action
Pause the campaign; lower limits only through an audited scope revision; review reconciliation (spent vs reserved). Tenant-wide caps are set in infra/.env.

## Never
Raise caps silently or reset the ledger.
