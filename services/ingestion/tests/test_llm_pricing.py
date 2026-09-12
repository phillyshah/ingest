"""What extraction costs, and which model it costs it on.

Campaign spend caps are enforced against these numbers, so a wrong rate is not a cosmetic problem: it either
stops a campaign early or lets it run past its cap.
"""

from __future__ import annotations

import pytest
from moveai_ingestion.llm import DEFAULT_EXTRACTION_MODEL, MODEL_PRICES_PER_MTOK, price_per_mtok


def test_the_default_is_the_cheapest_model_in_the_table():
    """Extraction is schema-constrained reading of one parsed document; it does not need an expensive model."""
    cheapest = min(MODEL_PRICES_PER_MTOK, key=lambda m: MODEL_PRICES_PER_MTOK[m][0])
    assert DEFAULT_EXTRACTION_MODEL == cheapest


def test_each_model_is_priced_from_its_own_row(monkeypatch):
    monkeypatch.delenv("PRICE_IN_PER_MTOK", raising=False)
    monkeypatch.delenv("PRICE_OUT_PER_MTOK", raising=False)
    for model, expected in MODEL_PRICES_PER_MTOK.items():
        assert price_per_mtok(model) == expected


def test_an_unknown_model_is_priced_at_the_dearest_known_rate(monkeypatch):
    """Undercharging an unknown model would let a campaign spend past its cap without the cap noticing."""
    monkeypatch.delenv("PRICE_IN_PER_MTOK", raising=False)
    monkeypatch.delenv("PRICE_OUT_PER_MTOK", raising=False)
    assert price_per_mtok("some-future-model") == max(MODEL_PRICES_PER_MTOK.values(), key=lambda p: p[0])


@pytest.mark.parametrize("model", [*MODEL_PRICES_PER_MTOK, "some-future-model"])
def test_an_explicit_price_overrides_the_table(monkeypatch, model):
    """A deployment on Bedrock or Vertex bills at different rates and must be able to say so."""
    monkeypatch.setenv("PRICE_IN_PER_MTOK", "0.5")
    monkeypatch.setenv("PRICE_OUT_PER_MTOK", "2.5")
    assert price_per_mtok(model) == (0.5, 2.5)


def test_output_is_dearer_than_input_everywhere():
    for into, out in MODEL_PRICES_PER_MTOK.values():
        assert out > into > 0
