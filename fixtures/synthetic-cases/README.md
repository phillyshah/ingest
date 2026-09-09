# Synthetic cases

Product test scenarios, not patients (spec §9, §20C/D). Ten or more per family. Each case carries an
`expected` block: routing status and the fields that must be reported missing. Every expectation is marked
`review: pending_clinical_lead_confirmation` because clinical reviewers, not engineers, own the expected routing.
`evals/planning/test_synthetic_cases.py` runs every case through the planner.

Field values are test inputs. They are not clinical thresholds; the placeholder packs contain none.
