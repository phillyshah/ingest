# Terminology fixtures

`icd10cm_order_sample_FY26.txt` and `..._FY27.txt` are small **sample extracts** in the layout of the official
CDC/NCHS ICD-10-CM order files (spec §17 [S2]). They contain only the codes needed by the fixtures. The importer
(`moveai_ingestion.terminology`) reads the official full files unchanged; replace the sample with the real release
files before production use and record their hash in `terminology_release.source_hash`.

Layout (fixed width, per CDC): order number (5) space code (7) space header flag (1) space short description (60) space long description.
Release effective dates come from `releases.yaml`, not from the code file.
