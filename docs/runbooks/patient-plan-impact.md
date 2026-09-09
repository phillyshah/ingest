# Runbook: Patient plan impact

## Symptoms
plan.review_required events; GET /plans/{id}/approved returns hold=true.

## Diagnosis
review_required_reason on plan_version; upstream withdrawal or expiry in dependency_edge/outbox_event.

## Action
The treating PT opens the case, creates a reassessment (new draft), and approves a new revision. The prior approved revision remains retrievable with hold=true until then; MoveAI presents the approved hold/fallback policy. Nothing is rewritten in place.

## Never
Silently mutate or auto-progress an assigned plan.
