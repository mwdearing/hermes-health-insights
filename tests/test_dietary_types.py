"""Tests for health_insights.dietary_types — TDD red-green cycle."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest

# Ensure the package root is importable regardless of how pytest is invoked.
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from health_insights import dietary_types as d


def _load_fixture():
    """Load the healthkit_dietary_types fixture."""
    fx_path = REPO / "tests" / "fixtures" / "healthkit_dietary_types.json"
    with open(fx_path, "r") as f:
        return json.load(f)["types"]


def test_fixture_coverage():
    """DIETARY_TYPES must contain all 39 fixture types with exact values."""
    fx = _load_fixture()
    assert isinstance(d.DIETARY_TYPES, dict)
    assert len(d.DIETARY_TYPES) == 39
    for entry in fx:
        tc = entry["type_code"]
        assert tc in d.DIETARY_TYPES
        info = d.DIETARY_TYPES[tc]
        assert info["healthkit_identifier"] == entry["healthkit_identifier"]
        assert info["canonical_unit"] == entry["canonical_unit"]
        assert info["dri_key"] == entry["dri_key"]


def test_normalize_g_to_mg():
    """Calcium: 1 g -> 1000 mg (canonical is mg)."""
    result = d.normalize_amount("dietary_calcium", 1, "g")
    assert result == (1000.0, "mg")


def test_normalize_mcg_to_mg():
    """Calcium: 500 mcg -> 0.5 mg."""
    result = d.normalize_amount("dietary_calcium", 500, "mcg")
    assert result == (0.5, "mg")


def test_normalize_cal_to_kcal():
    """Energy: 2000 Cal -> 2000 kcal (Cal is alias for kcal)."""
    result = d.normalize_amount("dietary_energy_consumed", 2000, "Cal")
    assert result == (2000.0, "kcal")


def test_normalize_kj_to_kcal():
    """Energy: 4184 kJ -> 1000 kcal (4.184 kJ per kcal)."""
    result = d.normalize_amount("dietary_energy_consumed", 4184, "kJ")
    assert result == (1000.0, "kcal")


def test_normalize_l_to_ml():
    """Water: 2 L -> 2000 mL (canonical is mL)."""
    result = d.normalize_amount("dietary_water", 2, "L")
    assert result == (2000.0, "mL")


def test_normalize_microsign_g():
    """Vitamin D: 25 µg -> 25 mcg (micro sign accepted as mcg)."""
    result = d.normalize_amount("dietary_vitamin_d", 25, "\u00b5g")
    assert result == (25.0, "mcg")


def test_normalize_incompatible_unit():
    """Energy with mg raises ValueError (weight unit for energy type)."""
    try:
        d.normalize_amount("dietary_energy_consumed", 5, "mg")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_normalize_unknown_unit():
    """Unknown unit raises ValueError."""
    try:
        d.normalize_amount("dietary_calcium", 5, "furlong")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_normalize_unknown_type():
    """Unknown type raises ValueError."""
    try:
        d.normalize_amount("dietary_nonexistent", 5, "g")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_untracked_dri_types():
    """untracked_dri_types returns sorted DRI types not in the given set."""
    present = {"dietary_protein", "dietary_iron", "dietary_zz"}
    result = d.untracked_dri_types(present)
    assert isinstance(result, list)
    assert result == sorted(result)
    assert "dietary_protein" not in result
    assert "dietary_iron" not in result
    assert "dietary_zz" not in result
    assert "dietary_calcium" in result


def test_untracked_dri_types_empty():
    """When every fixture type is present, result is empty list."""
    fx = _load_fixture()
    all_codes = {entry["type_code"] for entry in fx}
    result = d.untracked_dri_types(all_codes)
    assert result == []


def test_normalize_ug_alias():
    """'ug' (ASCII) is also accepted as mcg."""
    result = d.normalize_amount("dietary_vitamin_d", 10, "ug")
    assert result == (10.0, "mcg")


def test_normalize_g_to_mcg():
    """Vitamin A: 1 g -> 1000000 mcg (g to mcg)."""
    result = d.normalize_amount("dietary_vitamin_a", 1, "g")
    assert result == (1000000.0, "mcg")


def test_normalize_no_conversion_needed():
    """Value already in canonical unit returns unchanged."""
    result = d.normalize_amount("dietary_vitamin_d", 25, "mcg")
    assert result == (25.0, "mcg")


if __name__ == "__main__":
    unittest.main()
