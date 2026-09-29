"""Dietary type table and unit normaliser.

Stdlib-only. 39 HealthKit dietary types with canonical units and DRI keys.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# DIETARY_TYPES — {type_code: {healthkit_identifier, canonical_unit, dri_key}}
# ---------------------------------------------------------------------------

DIETARY_TYPES: Dict[str, Dict[str, Any]] = {
    "dietary_energy_consumed": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryEnergyConsumed",
        "canonical_unit": "kcal",
        "dri_key": None,
    },
    "dietary_fat_total": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryFatTotal",
        "canonical_unit": "g",
        "dri_key": "fat_total",
    },
    "dietary_fat_polyunsaturated": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryFatPolyunsaturated",
        "canonical_unit": "g",
        "dri_key": "fat_polyunsaturated",
    },
    "dietary_fat_monounsaturated": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryFatMonounsaturated",
        "canonical_unit": "g",
        "dri_key": "fat_monounsaturated",
    },
    "dietary_fat_saturated": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryFatSaturated",
        "canonical_unit": "g",
        "dri_key": "fat_saturated",
    },
    "dietary_cholesterol": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryCholesterol",
        "canonical_unit": "mg",
        "dri_key": "cholesterol",
    },
    "dietary_carbohydrates": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryCarbohydrates",
        "canonical_unit": "g",
        "dri_key": "carbohydrates",
    },
    "dietary_fiber": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryFiber",
        "canonical_unit": "g",
        "dri_key": "fiber",
    },
    "dietary_sugar": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietarySugar",
        "canonical_unit": "g",
        "dri_key": "sugar",
    },
    "dietary_sodium": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietarySodium",
        "canonical_unit": "mg",
        "dri_key": "sodium",
    },
    "dietary_protein": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryProtein",
        "canonical_unit": "g",
        "dri_key": "protein",
    },
    "dietary_calcium": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryCalcium",
        "canonical_unit": "mg",
        "dri_key": "calcium",
    },
    "dietary_iron": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryIron",
        "canonical_unit": "mg",
        "dri_key": "iron",
    },
    "dietary_potassium": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryPotassium",
        "canonical_unit": "mg",
        "dri_key": "potassium",
    },
    "dietary_vitamin_a": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminA",
        "canonical_unit": "mcg",
        "dri_key": "vitamin_a",
    },
    "dietary_vitamin_b6": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminB6",
        "canonical_unit": "mg",
        "dri_key": "vitamin_b6",
    },
    "dietary_vitamin_b12": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminB12",
        "canonical_unit": "mcg",
        "dri_key": "vitamin_b12",
    },
    "dietary_vitamin_c": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminC",
        "canonical_unit": "mg",
        "dri_key": "vitamin_c",
    },
    "dietary_vitamin_d": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminD",
        "canonical_unit": "mcg",
        "dri_key": "vitamin_d",
    },
    "dietary_vitamin_e": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminE",
        "canonical_unit": "mg",
        "dri_key": "vitamin_e",
    },
    "dietary_vitamin_k": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryVitaminK",
        "canonical_unit": "mcg",
        "dri_key": "vitamin_k",
    },
    "dietary_thiamin": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryThiamin",
        "canonical_unit": "mg",
        "dri_key": "thiamin",
    },
    "dietary_riboflavin": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryRiboflavin",
        "canonical_unit": "mg",
        "dri_key": "riboflavin",
    },
    "dietary_niacin": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryNiacin",
        "canonical_unit": "mg",
        "dri_key": "niacin",
    },
    "dietary_folate": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryFolate",
        "canonical_unit": "mcg",
        "dri_key": "folate",
    },
    "dietary_biotin": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryBiotin",
        "canonical_unit": "mcg",
        "dri_key": "biotin",
    },
    "dietary_pantothenic_acid": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryPantothenicAcid",
        "canonical_unit": "mg",
        "dri_key": "pantothenic_acid",
    },
    "dietary_phosphorus": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryPhosphorus",
        "canonical_unit": "mg",
        "dri_key": "phosphorus",
    },
    "dietary_iodine": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryIodine",
        "canonical_unit": "mcg",
        "dri_key": "iodine",
    },
    "dietary_magnesium": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryMagnesium",
        "canonical_unit": "mg",
        "dri_key": "magnesium",
    },
    "dietary_zinc": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryZinc",
        "canonical_unit": "mg",
        "dri_key": "zinc",
    },
    "dietary_selenium": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietarySelenium",
        "canonical_unit": "mcg",
        "dri_key": "selenium",
    },
    "dietary_copper": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryCopper",
        "canonical_unit": "mcg",
        "dri_key": "copper",
    },
    "dietary_manganese": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryManganese",
        "canonical_unit": "mg",
        "dri_key": "manganese",
    },
    "dietary_chromium": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryChromium",
        "canonical_unit": "mcg",
        "dri_key": "chromium",
    },
    "dietary_molybdenum": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryMolybdenum",
        "canonical_unit": "mcg",
        "dri_key": "molybdenum",
    },
    "dietary_chloride": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryChloride",
        "canonical_unit": "mg",
        "dri_key": None,
    },
    "dietary_caffeine": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryCaffeine",
        "canonical_unit": "mg",
        "dri_key": None,
    },
    "dietary_water": {
        "healthkit_identifier": "HKQuantityTypeIdentifierDietaryWater",
        "canonical_unit": "mL",
        "dri_key": "water",
    },
}

# ---------------------------------------------------------------------------
# Unit conversion tables
# ---------------------------------------------------------------------------

# Mass conversions: {unit: factor_to_canonical}
# All mass types use g, mg, mcg as possible inputs.
_KG = 1000.0  # g to kg
_MG = 1.0  # mg to mg
_MCG = 0.001  # mcg to mg (but we want mcg as canonical for some types)

# We need a more nuanced approach: each type has its own canonical unit.
# Conversion factors are relative to grams for mass, kcal for energy, mL for volume.

# Mass: g -> mg -> mcg (each step is 1000x)
_MASS_TO_G = {"g": 1.0, "mg": 0.001, "mcg": 1e-6, "\u00b5g": 1e-6, "ug": 1e-6}
# Energy: kcal is canonical; Cal is alias; kJ converts via 4.184
_ENERGY_TO_KCAL = {"kcal": 1.0, "Cal": 1.0, "kJ": 1.0 / 4.184}
# Volume: mL is canonical; L converts via 1000
_VOLUME_TO_ML = {"mL": 1.0, "L": 1000.0}

# Canonical units grouped by conversion family
_MASS_CANONICALS = {"g", "mg", "mcg"}
_ENERGY_CANONICALS = {"kcal"}
_VOLUME_CANONICALS = {"mL"}


def normalize_amount(type_code: str, value: float, unit: str) -> Tuple[float, str]:
    """Convert *value* from *unit* to the canonical unit for *type_code*.

    Returns ``(value_in_canonical_unit, canonical_unit)``.

    Raises
    ------
    ValueError
        If *type_code* is unknown, *unit* is unknown, or *unit* is
        incompatible with the type's expected dimension (e.g. mg for energy).
    """
    if type_code not in DIETARY_TYPES:
        raise ValueError(f"Unknown dietary type: {type_code}")

    info = DIETARY_TYPES[type_code]
    canonical = info["canonical_unit"]

    # Determine which conversion family this type belongs to.
    converted: float = 0.0
    if canonical == "kcal":
        # Energy
        if unit not in _ENERGY_TO_KCAL:
            raise ValueError(f"Unknown or incompatible unit '{unit}' for energy type '{type_code}'")
        converted = value * _ENERGY_TO_KCAL[unit]
    elif canonical == "mL":
        # Volume
        if unit not in _VOLUME_TO_ML:
            raise ValueError(f"Unknown or incompatible unit '{unit}' for volume type '{type_code}'")
        converted = value * _VOLUME_TO_ML[unit]
    elif canonical in _MASS_CANONICALS:
        # Mass — g, mg, mcg
        if unit not in _MASS_TO_G:
            raise ValueError(f"Unknown or incompatible unit '{unit}' for mass type '{type_code}'")
        # Convert input to grams, then grams to canonical.
        grams = value * _MASS_TO_G[unit]
        if canonical == "g":
            converted = grams
        elif canonical == "mg":
            converted = grams * 1000.0
        elif canonical == "mcg":
            converted = grams * 1_000_000.0
    else:
        raise ValueError(f"Unknown canonical unit '{canonical}' for type '{type_code}'")

    return (round(converted, 6), canonical)


def untracked_dri_types(present_type_codes: set) -> list:
    """Return sorted type codes that have a *dri_key* but are not in *present_type_codes*.

    Only considers types that are in DIETARY_TYPES; codes not in the table
    are silently ignored.
    """
    result = [
        tc
        for tc, info in DIETARY_TYPES.items()
        if info["dri_key"] is not None and tc not in present_type_codes
    ]
    return sorted(result)
