"""Tests for health_insights.ul_attribution module."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path.cwd()))
from health_insights.ul_attribution import Contribution, ULFinding, compare_to_ul  # noqa: E402


def _C(day: str, nutrient: str, amount, unit: str, source: str, form=None):
    """Helper to create Contribution with Decimal amounts."""
    return Contribution(
        day=day,
        nutrient=nutrient,
        amount=None if amount is None else Decimal(str(amount)),
        unit=unit,
        source=source,
        form=form,
    )


# ---------------------------------------------------------------------------
# Rule: empty input returns empty list
# ---------------------------------------------------------------------------
def test_empty_input_returns_empty():
    dri = {}
    result = compare_to_ul([], dri)
    assert result == []


# ---------------------------------------------------------------------------
# Rule: no UL (None) -> status "no_ul"
# ---------------------------------------------------------------------------
def test_no_ul_status():
    dri = {
        "protein": {"unit": "g", "ul": None, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [_C("2026-01-01", "protein", "100", "g", "food")]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.nutrient == "protein"
    assert f.status == "no_ul"
    assert f.high_days == []
    assert f.undetermined_days == []
    assert f.mean_daily is None
    assert f.days_counted == 0


# ---------------------------------------------------------------------------
# Rule: iron (food+supplements, any form) -> both sources count
# ---------------------------------------------------------------------------
def test_iron_food_plus_supplements_combined():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-01", "iron", "30", "mg", "food"),
        _C("2026-10-01", "iron", "20", "mg", "supplement", "ferrous bisglycinate"),
        _C("2026-10-02", "iron", "20", "mg", "food"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "above"
    assert f.high_days == ["2026-10-01"]
    assert f.undetermined_days == []
    assert f.mean_daily == Decimal("35")
    assert f.days_counted == 2


# ---------------------------------------------------------------------------
# Rule: magnesium (supplements only) -> food never counts
# ---------------------------------------------------------------------------
def test_supplements_only_food_ignored():
    dri = {
        "magnesium": {"unit": "mg", "ul": 350, "ul_scope": "supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-01", "magnesium", "500", "mg", "food"),
        _C("2026-10-01", "magnesium", "300", "mg", "supplement", "citrate"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "within"
    assert f.high_days == []
    assert f.undetermined_days == []
    assert f.mean_daily == Decimal("300")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: supplements only + unknown source -> day is undetermined
# ---------------------------------------------------------------------------
def test_supplements_only_unknown_source_undetermined():
    dri = {
        "magnesium": {"unit": "mg", "ul": 350, "ul_scope": "supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-01", "magnesium", "300", "mg", "supplement", "citrate"),
        _C("2026-10-02", "magnesium", "400", "mg", "unknown"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "undetermined"
    assert f.high_days == []
    assert f.undetermined_days == ["2026-10-02"]
    assert f.mean_daily == Decimal("300")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: form-specific UL (folic acid) -> case-insensitive form matching
# ---------------------------------------------------------------------------
def test_form_specific_case_insensitive():
    dri = {
        "folate": {"unit": "mcg", "ul": 1000, "ul_scope": "supplements", "form": "folic acid"},
    }
    contribs = [
        _C("2026-10-01", "folate", "1200", "mcg", "supplement", "Folic Acid"),
        _C("2026-10-02", "folate", "2000", "mcg", "supplement", "methylfolate"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "above"
    assert f.high_days == ["2026-10-01"]
    assert f.undetermined_days == []
    assert f.mean_daily == Decimal("1200")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: form-specific UL + form None -> day is undetermined
# ---------------------------------------------------------------------------
def test_form_specific_none_form_undetermined():
    dri = {
        "folate": {"unit": "mcg", "ul": 1000, "ul_scope": "supplements", "form": "folic acid"},
    }
    contribs = [
        _C("2026-10-01", "folate", "1200", "mcg", "supplement", "Folic Acid"),
        _C("2026-10-03", "folate", "800", "mcg", "supplement", None),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    # high day takes priority over undetermined
    assert f.status == "above"
    assert f.high_days == ["2026-10-01"]
    assert f.undetermined_days == ["2026-10-03"]


# ---------------------------------------------------------------------------
# Rule: unknown amount -> day is undetermined (never zero)
# ---------------------------------------------------------------------------
def test_unknown_amount_undetermined():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-05", "iron", None, "mg", "food"),
        _C("2026-10-07", "iron", "10", "mg", "food"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "undetermined"
    assert f.high_days == []
    assert f.undetermined_days == ["2026-10-05"]
    assert f.mean_daily == Decimal("10")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: unit mismatch -> day is undetermined (never converted)
# ---------------------------------------------------------------------------
def test_unit_mismatch_undetermined():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-06", "iron", "0.05", "g", "food"),
        _C("2026-10-07", "iron", "10", "mg", "food"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "undetermined"
    assert f.high_days == []
    assert f.undetermined_days == ["2026-10-06"]
    assert f.mean_daily == Decimal("10")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: within limits -> status "within"
# ---------------------------------------------------------------------------
def test_within_limits():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [_C("2026-10-08", "iron", "10", "mg", "food")]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.status == "within"
    assert f.high_days == []
    assert f.undetermined_days == []
    assert f.mean_daily == Decimal("10")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: multiple nutrients -> one finding each, sorted by nutrient name
# ---------------------------------------------------------------------------
def test_multiple_nutrients_sorted():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
        "magnesium": {"unit": "mg", "ul": 350, "ul_scope": "supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-01", "magnesium", "300", "mg", "supplement"),
        _C("2026-10-01", "iron", "20", "mg", "food"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 2
    assert result[0].nutrient == "iron"
    assert result[1].nutrient == "magnesium"


# ---------------------------------------------------------------------------
# Rule: undetermined days never count toward high days
# ---------------------------------------------------------------------------
def test_undetermined_days_not_high_days():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-05", "iron", None, "mg", "food"),
        _C("2026-10-06", "iron", "0.05", "g", "food"),
        _C("2026-10-07", "iron", "10", "mg", "food"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.undetermined_days == ["2026-10-05", "2026-10-06"]
    assert f.high_days == []
    assert f.status == "undetermined"
    assert f.mean_daily == Decimal("10")
    assert f.days_counted == 1


# ---------------------------------------------------------------------------
# Rule: mean_daily computed over determined days only
# ---------------------------------------------------------------------------
def test_mean_daily_over_determined_days():
    dri = {
        "iron": {"unit": "mg", "ul": 45, "ul_scope": "food+supplements", "form": "any"},
    }
    contribs = [
        _C("2026-10-01", "iron", "20", "mg", "food"),
        _C("2026-10-02", "iron", None, "mg", "food"),
        _C("2026-10-03", "iron", "40", "mg", "food"),
    ]
    result = compare_to_ul(contribs, dri)
    assert len(result) == 1
    f = result[0]
    assert f.mean_daily == Decimal("30")
    assert f.days_counted == 2
