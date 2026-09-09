# Runbook: Rights revocation

## Symptoms
A rights holder revokes or a licence expires; rights.expired events; assets show rights_hold/unavailable.

## Diagnosis
`select * from rights_grant where revoked_at is not null or expires_at < now()`. The scheduler expires grants automatically; a rights reviewer can revoke via POST /v1/reviews rights_deny.

## Action
Propagation marks dependent versions withdrawn and routes affected approved plans to review_required (visible in GET /plans/{id}/approved as hold=true). Text-only alternatives may be chosen by a PT; never substitute silently. Retain audit metadata.

## Never
Delete patient plan history or continue new assignment of revoked content.
