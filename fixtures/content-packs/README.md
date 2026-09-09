# Content packs

Each YAML file is one clinical content pack: condition, sources, claims, exercise concepts/variants, clinical-use
records, typed rules, protocols (phases + windows), and population-applicability records.

**Every pack here is `unsigned_placeholder`.** They exist so the engine can be built and demonstrated. They are not
treatment protocols. Doses are `null` with a reason. Phase windows carry no numbers unless a cited source states them.
Routing rules are limited to `required_input`, `concern_screen`, and `block_for_review`, plus rule *slots* that a
clinical lead must author and sign before they become executable.

`demo_synthetic.yaml` is a deliberately non-clinical pack with made-up numbers, used only to exercise the
`draft_ready -> approve -> adapter` path in tests and `make demo`. Its condition code is `demo_synthetic`.

Source references cite spec §17 documents as *reference-only* (rights unresolved). Nothing has been copied from them.
