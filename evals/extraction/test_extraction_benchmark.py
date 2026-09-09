"""Extraction benchmark harness: each permitted fixture's sidecar is the annotated expectation. Reports field-level
exact-match accuracy for the (mock) model and asserts critical fields are never guessed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.extract import run_extraction
from moveai_ingestion.llm import MockExtractionModel
from moveai_ingestion.parse import parse

CASES = sorted(p for p in (FIXTURES / "permitted-sources").glob("*") if p.suffix in (".html", ".pdf") or p.name.endswith(".ocr.json"))


@pytest.mark.parametrize("path", CASES, ids=[p.name for p in CASES])
def test_fixture_extracts_with_provenance(path: Path):
    ct = {".html": "text/html", ".pdf": "application/pdf", ".json": "application/json"}[path.suffix]
    doc = parse(path.read_bytes(), ct, "scanned_pdf" if path.name.endswith(".ocr.json") else "html")
    out = run_extraction(doc, MockExtractionModel(), source_hint=str(path))
    for ex in out.result.exercises:
        assert ex.locator, ex.source_exercise_name
        for name, f in ex.dose.items():
            assert f.value is None or f.locator, f"{ex.source_exercise_name}.{name} has a number without a locator"
            if f.value is None:
                assert f.null_reason, f"{ex.source_exercise_name}.{name} null without reason"


def test_benchmark_accuracy_report(capsys):
    total = correct = 0
    for path in CASES:
        sidecar = path.parent / (path.name.split(".")[0] + ".extraction.json")
        if not sidecar.exists():
            continue
        expected = json.loads(sidecar.read_text())
        ct = {".html": "text/html", ".pdf": "application/pdf", ".json": "application/json"}[path.suffix]
        doc = parse(path.read_bytes(), ct, "scanned_pdf" if path.name.endswith(".ocr.json") else "html")
        got = run_extraction(doc, MockExtractionModel(), source_hint=str(path)).result.model_dump(mode="json")
        for e_exp, e_got in zip(expected["exercises"], got["exercises"], strict=False):
            for k in ("source_exercise_name", "assistance", "steps"):
                total += 1
                correct += e_exp.get(k) == e_got.get(k)
    print(f"\nextraction benchmark: {correct}/{total} noncritical fields exact-match ({100 * correct / max(total, 1):.0f}%)")
    assert total > 0
