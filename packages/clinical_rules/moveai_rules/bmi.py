"""BMI computation and CDC adult classification (spec §19). Classification is data: the boundaries come from a
versioned segmentation definition, never from constants scattered in planner code."""
from __future__ import annotations

from dataclasses import dataclass

CDC_ADULT_BMI_V2024 = {
    "dimension": "bmi",
    "authority": "CDC Adult BMI Categories",
    "authority_version": "2024-adult",
    "population_scope": "adults aged 20 years and older",
    "source_url": "https://www.cdc.gov/bmi/adult-calculator/bmi-categories.html",
    "boundaries": [
        {"class": "underweight", "min": None, "max": 18.5, "min_inclusive": True, "max_inclusive": False, "unit": "kg/m2"},
        {"class": "healthy_weight", "min": 18.5, "max": 25.0, "min_inclusive": True, "max_inclusive": False, "unit": "kg/m2"},
        {"class": "overweight", "min": 25.0, "max": 30.0, "min_inclusive": True, "max_inclusive": False, "unit": "kg/m2"},
        {"class": "class_1_obesity", "min": 30.0, "max": 35.0, "min_inclusive": True, "max_inclusive": False, "unit": "kg/m2"},
        {"class": "class_2_obesity", "min": 35.0, "max": 40.0, "min_inclusive": True, "max_inclusive": False, "unit": "kg/m2"},
        {"class": "class_3_obesity", "min": 40.0, "max": None, "min_inclusive": True, "max_inclusive": False, "unit": "kg/m2"},
    ],
    "min_age_years": 20,
}

LENGTH_TO_M = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "ft": 0.3048}
MASS_TO_KG = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "st": 6.35029318}


def to_meters(value: float, unit: str) -> float:
    if unit not in LENGTH_TO_M:
        raise ValueError(f"unsupported length unit {unit!r}")
    return value * LENGTH_TO_M[unit]


def to_kg(value: float, unit: str) -> float:
    if unit not in MASS_TO_KG:
        raise ValueError(f"unsupported mass unit {unit!r}")
    return value * MASS_TO_KG[unit]


def compute_bmi(height_m: float, weight_kg: float) -> float:
    if height_m <= 0 or weight_kg <= 0:
        raise ValueError("height and weight must be positive")
    return weight_kg / (height_m * height_m)  # unrounded


@dataclass(frozen=True)
class Classification:
    value: float | None
    klass: str | None            # None when not classifiable
    reason: str | None
    definition_version: str


def classify_bmi(bmi: float | None, age_years: float | None, definition: dict = CDC_ADULT_BMI_V2024) -> Classification:
    ver = f"{definition['authority']} {definition['authority_version']}"
    if bmi is None:
        return Classification(None, None, "bmi not available", ver)
    if age_years is None:
        return Classification(bmi, None, "age unknown; adult classification requires age >= 20", ver)
    if age_years < definition["min_age_years"]:
        return Classification(bmi, None, "age under 20: CDC uses age- and sex-specific assessment; pediatric out of scope", ver)
    for b in definition["boundaries"]:
        lo = b["min"] is None or (bmi >= b["min"] if b["min_inclusive"] else bmi > b["min"])
        hi = b["max"] is None or (bmi <= b["max"] if b["max_inclusive"] else bmi < b["max"])
        if lo and hi:
            return Classification(bmi, b["class"], None, ver)
    return Classification(bmi, None, "value outside defined boundaries", ver)
