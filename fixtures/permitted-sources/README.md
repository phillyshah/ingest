# Permitted source fixtures

All files here are **owned synthetic fixtures** written for this repository. They imitate the *structure* of
clinical documents (headings, phase-linked dose tables, footnotes, multi-column layout, OCR noise) so the parser and
validators can be tested. They contain no copied text and no real clinical recommendations.

| File | Exercises |
| --- | --- |
| `owned_demo_protocol.html` | HTML with a phase-linked dose table and anchors; rights fully allowed. Drives the demo pack. |
| `owned_demo_protocol.pdf` | Text PDF rendering of the same content, with a page boundary inside the dose table. |
| `scanned_ambiguous.ocr.json` | OCR-stub output for a "scanned" page where `1–2` vs `12` is ambiguous (spec §13 test 4). |
| `injection_attempt.html` | Contains "ignore previous instructions"; the extractor must treat it as data (spec §13 test 9). |
| `headingless_dose_table.html` | Dose table whose phase heading was removed; doses must not leak across phases (spec §13 test 3). |
| `rights_restricted_graphic.html` | Text allowed, graphic rights unknown; the graphic goes to `rights_hold`, text proceeds. |
| `derivative_copy.html` | Byte-different copy of the demo protocol claiming the same underlying guideline (duplicate grouping). |
| `owned_demo_protocol_with_photo.pdf` | One page, one heading, one embedded PNG photo — the minimal case for testing embedded-image extraction and page-based association to the exercise it belongs to. Built with `reportlab` + `Pillow` (not a project dependency; regenerate with `scripts/dev/make_photo_pdf_fixture.py` if it ever needs to change). |

Each `.html`/`.pdf` has a sidecar `<name>.extraction.json`: the deterministic output the mock extraction model
returns for it (ADR-0006). Sidecars are what a clinician-annotated benchmark would hold.
