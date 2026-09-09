# Runbook: Terminology update

## Symptoms
A new ICD-10-CM release (e.g. FY27 on 2026-10-01) must be imported; scope previews show unresolved codes.

## Diagnosis
`select * from terminology_release`; compare effective dates with the CDC release page (spec §17 S2).

## Action
Download the official order file, place it under fixtures/terminology (or an uploads path), add the release to releases.yaml with effective dates, run the importer (moveai_ingestion.terminology.import_releases), and re-run scope previews. Codes resolve by service date, never by latest download.

## Never
Hardcode codes or laterality, or edit an existing release's effective dates without evidence.
