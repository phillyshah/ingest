# Runbook: Clinical correction

## Symptoms
A clinical lead finds an error in an approved variant, clinical use, rule, or protocol.

## Diagnosis
Locate the version in the review queue detail; check dependency_edge downstream impact (campaign Exercises & Evidence tab).

## Action
Use POST /v1/reviews decision=withdraw (clinical_lead) with a reason; propagation routes affected plans to review. Author the corrected version (edit creates a new pending version); a different reviewer approves; publish a new catalog release.

## Never
Update an approved row in place (the database refuses) or approve your own correction.
