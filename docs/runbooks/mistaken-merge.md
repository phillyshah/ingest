# Runbook: Mistaken merge

## Symptoms
Two distinct variants were merged; the merged-away version shows superseded with duplicate_of_entity_id.

## Diagnosis
review_event history for the version; dependency_edge derived_from link to the merge target.

## Action
The merged-away version is immutable but not deleted: create a new version from it via edit (changes at least one field), route through review, and withdraw the wrong target link if content was wrong. Merges that differ in assistance/load/range/setting are refused by the API in the first place.

## Never
Delete versions or rewrite review history.
