"""Tests for nutrition-gap concern rule.

Synthetic data only — no real paths, no real data directory, no real DB.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date as Date, timedelta

import pytest

from health_insights import concerns
from health_insights.concern_rules import nutrition_rules as m


# ── helpers ──────────────────────────────────────────────────────────────────

DRI = {
    "sex": "male",
    "age_years": 35,
    "activity_factor": 1.4,
    "rules": {"low_share": 0.70, "min_logged_days": 4, "partial_day_kcal": 1200},
    "nutrients": {
        "dietary_energy_consumed": {"apple": "dietary_energy_consumed", "unit": "kcal", "label": "Energy"},
        "folate": {"apple": "dietary_folate", "unit": "mcg", "target": 400, "label": "Folate"},
        "zinc": {"apple": "dietary_zinc", "unit": "mg", "target": 11, "label": "Zinc"},
        "vitamin_c": {"apple": "dietary_vitamin_c", "unit": "mg", "target": 90, "label": "Vitamin C"},
        "sodium": {"apple": "dietary_sodium", "unit": "mg", "type": "limit", "ul": 2300, "label": "Sodium"},
        "magnesium": {"apple": "dietary_magnesium", "unit": "mg", "target": 420, "label": "Magnesium"},
    },
}


def _make_db(days_data: dict[int, dict[str, float]]) -> str:
    """Create a temp SQLite DB with samples.

    *days_data* maps days-before-ref to {type_code: value}.
    Every day gets dietary_energy_consumed=2000 so it counts as logged.
    """
    path = tempfile.mktemp(suffix=".sqlite")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
        "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT, "
        "UNIQUE (type_code, start_time, value))"
    )
    ref = Date(2031, 9, 20)
    n = 0
    for back, vals in days_data.items():
        d = ref - timedelta(days=back)
        t = f"{d.isoformat()}T18:00:00Z"
        vals = dict(vals)
        vals.setdefault("dietary_energy_consumed", 2000)
        for tc, v in vals.items():
            con.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, "
                "start_time, end_time, value, unit) VALUES ('1',?,?,?,?,?,'x')",
                (tc, f"s{n}", t, t, v),
            )
            n += 1
    con.commit()
    con.close()
    return path


def _write_dri(path: str) -> None:
    import yaml

    yaml.safe_dump(DRI, open(path, "w"))


# ── tests ────────────────────────────────────────────────────────────────────

class TestNutritionGapFindingsDirect:
    """Direct calls to nutrition_gap_findings with synthetic data."""

    def test_01_persistent_low_nutrient_qualifies(self):
        """Folate 60 mcg every one of 10 logged days → finding."""
        days_data = {b: {"dietary_folate": 60} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert len(findings) == 1
            f = findings[0]
            assert f.id == "nutrition_gap"
            assert f.level == 1
            assert f.title == "Some nutrients running low in your food log"
            assert "Folate" in f.evidence
            assert "%" in f.evidence
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_02_low_nutrient_excluded_too_few_own_days(self):
        """Zinc low on only 2 days (below min_logged_days=4) → excluded."""
        days_data = {b: {} for b in range(10)}
        days_data[0] = {"dietary_zinc": 2}
        days_data[1] = {"dietary_zinc": 2}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            # zinc is low but has only 2 own-days → excluded → no finding
            assert len(findings) == 0
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_03_adequate_nutrient_no_finding(self):
        """Vitamin C at 100% of target every day → no finding."""
        days_data = {b: {"dietary_vitamin_c": 90} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert len(findings) == 0
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_04_too_few_overall_logged_days(self):
        """Only 2 logged days total → report_json gate fires → no crash, empty."""
        days_data = {0: {"dietary_folate": 60}, 1: {"dietary_folate": 60}}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert findings == []
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_05_missing_table_no_crash(self):
        """DB with no samples table → empty, no crash."""
        db = tempfile.mktemp(suffix=".sqlite")
        sqlite3.connect(db).close()
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert findings == []
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_06_empty_db_no_crash(self):
        """Completely empty file → no crash, empty."""
        db = tempfile.mktemp(suffix=".sqlite")
        open(db, "w").close()
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert findings == []
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_07_multiple_qualifying_sorted_by_pct(self):
        """Two low nutrients → single Finding with both sorted lowest pct first."""
        # Folate: 200 mcg target=400 → 50%
        # Magnesium: 200 target=420 → ~48%
        days_data = {b: {"dietary_folate": 200, "dietary_magnesium": 200} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert len(findings) == 1
            evidence = findings[0].evidence
            # magnesium (~48%) should appear before folate (50%)
            assert evidence.index("Magnesium") < evidence.index("Folate")
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_08_nutrient_at_70_pct_not_low(self):
        """Nutrient exactly at 70% of target → status is 'ok', not 'low'."""
        # zinc target=11, 70% = 7.7 → use 8 (rounds to 73%)
        # Use 7.7 exact: 7.7/11*100 = 70 → pct=70, pct<70 is False
        days_data = {b: {"dietary_zinc": 7.7} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert len(findings) == 0
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_09_advice_no_specific_dose(self):
        """Advice must never prescribe a specific dose or amount."""
        days_data = {b: {"dietary_folate": 60} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert len(findings) == 1
            advice_lower = findings[0].advice.lower()
            assert "take " not in advice_lower
            import re

            assert not re.search(r"\d+\s*(mg|mcg|iu)\b", advice_lower)
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_10_source_is_heuristic(self):
        """Source string is 'heuristic (DRI comparison)'."""
        days_data = {b: {"dietary_folate": 60} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            findings = m.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
            assert len(findings) == 1
            assert findings[0].source == "heuristic (DRI comparison)"
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)


class TestNutritionGapThroughEvaluate:
    """Tests calling through concerns.evaluate() to catch the date-object bug."""

    def test_11_via_concerns_evaluate_date_object(self):
        """concerns.evaluate() passes a date OBJECT, not a string.
        The rule must convert it back to a string before calling report_json().
        """
        days_data = {b: {"dietary_folate": 60} for b in range(10)}
        db = _make_db(days_data)
        dri = tempfile.mktemp(suffix=".yaml")
        _write_dri(dri)
        try:
            # concerns.evaluate expects a ref_date STRING and internally converts
            # it to a date object before passing to each rule fn(db, date_obj).
            # Our rule must handle the date object.
            findings = concerns.evaluate(db, "2031-09-20")
            ids = [f.id for f in findings]
            assert "nutrition_gap" in ids
        finally:
            import os

            os.unlink(dri)
            os.unlink(db)

    def test_12_registered_in_default_rules(self):
        """'nutrition' appears in DEFAULT_RULES names."""
        names = [n for n, _ in concerns.DEFAULT_RULES]
        assert "nutrition" in names

    def test_13_no_finding_when_all_adequate_through_evaluate(self):
        """All nutrients adequate → evaluate returns no nutrition_gap."""
        days_data = {b: {"dietary_vitamin_c": 90} for b in range(10)}
        db = _make_db(days_data)
        try:
            findings = concerns.evaluate(db, "2031-09-20")
            ids = [f.id for f in findings]
            assert "nutrition_gap" not in ids
        finally:
            os.unlink(db)

    def test_14_no_crash_on_nonexistent_db_through_evaluate(self):
        """Nonexistent DB path → no crash through evaluate."""
        findings = concerns.evaluate("/tmp/nonexistent_db_w10_test.sqlite", "2031-09-20")
        ids = [f.id for f in findings]
        assert "nutrition_gap" not in ids

    def test_15_no_crash_on_missing_table_through_evaluate(self):
        """DB with no samples table → no crash through evaluate."""
        db = tempfile.mktemp(suffix=".sqlite")
        sqlite3.connect(db).close()
        try:
            findings = concerns.evaluate(db, "2031-09-20")
            ids = [f.id for f in findings]
            assert "nutrition_gap" not in ids
        finally:
            os.unlink(db)
