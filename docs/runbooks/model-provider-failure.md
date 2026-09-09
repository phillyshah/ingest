# Runbook: Model provider failure

## Symptoms
extract stage fails with unexpected/schema_invalid; costs stop accruing; ANTHROPIC provider errors in worker logs.

## Diagnosis
schema_invalid is permanent (model returned something outside the extraction schema): inspect the checkpoint and the source for prompt-injection text. Provider outages retry with backoff.

## Action
Set EXTRACTION_MODEL=mock-1 to keep fixtures flowing, or pause campaigns. After recovery, Retry failed. Verify the provider is authorized for this data use before re-enabling (spec §11).

## Never
Relax schema validation, allow tools, or send patient data to any model.
