"""Tests for lab out-of-range concern rule (labs_rules.py).

Synthetic data only. No real paths, no real DB.
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date

from health_insights.concern_rules.labs_rules import labs_findings


def _make_db(rows: list[tuple]) -> str:
    """Create a temp SQLite DB with lab_results table and *rows*.

    rows: list of (obs_id, name, effective_date, value_num, unit, ref_low, ref_high, flag).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    con = sqlite3.connect(tmp.name)
    con.execute(
        "CREATE TABLE lab_results ("
        "obs_id TEXT PRIMARY KEY, loinc TEXT, name TEXT, category TEXT, "
        "effective_date TEXT, value_num REAL, unit TEXT, value_text TEXT, "
        "ref_low REAL, ref_high REAL, ref_text TEXT, flag TEXT, "
        "imported_at TEXT, source_file TEXT)"
    )
    for i, (obs_id, name, d, v, u, lo, hi, flag) in enumerate(rows):
        con.execute(
            "INSERT INTO lab_results "
            "(obs_id, loinc, name, effective_date, value_num, unit, ref_low, ref_high, flag) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (obs_id, f"L{i}", name, d, v, u, lo, hi, flag),
        )
    con.commit()
    con.close()
    return tmp.name


def _date(days_ago: int) -> str:
    """Return an ISO date string *days_ago* before REF."""
    return (date(2031, 7, 10) - __import__("datetime").timedelta(days=days_ago)).isoformat()


# --- 1. No findings when normal results only ---
def test_no_findings_normal():
    db = _make_db([("o1", "Glucose", _date(10), 90.0, "mg/dL", 70.0, 99.0, "NORMAL")])
    try:
        results = labs_findings(db, "2031-07-10")
        assert results == []
    finally:
        import os
        os.unlink(db)


# --- 2. No findings when UNKNOWN flag only ---
def test_no_findings_unknown():
    db = _make_db([("o1", "Widget", _date(10), None, None, None, None, "UNKNOWN")])
    try:
        results = labs_findings(db, "2031-07-10")
        assert results == []
    finally:
        import os
        os.unlink(db)


# --- 3. In-window HIGH triggers finding ---
def test_high_in_window():
    db = _make_db([("o1", "Glucose", _date(10), 145.0, "mg/dL", 70.0, 99.0, "HIGH")])
    try:
        results = labs_findings(db, "2031-07-10")
        assert len(results) == 1
        assert results[0].id == "lab_out_of_range"
        assert results[0].level == 2
        assert results[0].title == "New lab result outside its reference range"
        assert "Glucose" in results[0].evidence
        assert "145" in results[0].evidence
        assert "99" in results[0].evidence
        assert "reference range" in results[0].source.lower()
        assert "clinician" in results[0].advice.lower()
        assert "critical" not in results[0].advice.lower()
        assert "critical" not in results[0].title.lower()
    finally:
        import os
        os.unlink(db)


# --- 4. In-window LOW triggers finding ---
def test_low_in_window():
    db = _make_db([("o1", "Potassium", _date(5), 3.0, "mmol/L", 3.5, 5.1, "LOW")])
    try:
        results = labs_findings(db, "2031-07-10")
        assert len(results) == 1
        assert results[0].id == "lab_out_of_range"
        assert results[0].level == 2
        assert "Potassium" in results[0].evidence
        assert "3.0" in results[0].evidence
        assert "low" in results[0].evidence.lower()
    finally:
        import os
        os.unlink(db)


# --- 5. Out-of-window (>90 days) excluded ---
def test_out_of_window_excluded():
    db = _make_db([("o1", "Glucose", _date(120), 145.0, "mg/dL", 70.0, 99.0, "HIGH")])
    try:
        results = labs_findings(db, "2031-07-10")
        assert results == []
    finally:
        import os
        os.unlink(db)


# --- 6. Exactly 90 days ago included (boundary) ---
def test_boundary_90_days_included():
    db = _make_db([("o1", "Glucose", _date(90), 145.0, "mg/dL", 70.0, 99.0, "HIGH")])
    try:
        results = labs_findings(db, "2031-07-10")
        assert len(results) == 1
        assert results[0].id == "lab_out_of_range"
        assert "Glucose" in results[0].evidence
    finally:
        import os
        os.unlink(db)


# --- 7. Multiple qualifying results in one evidence string ---
def test_multiple_results_one_finding():
    db = _make_db([
        ("o1", "Glucose", _date(10), 145.0, "mg/dL", 70.0, 99.0, "HIGH"),
        ("o2", "ALT", _date(8), 80.0, "U/L", 7.0, 55.0, "HIGH"),
    ])
    try:
        results = labs_findings(db, "2031-07-10")
        assert len(results) == 1
        assert results[0].id == "lab_out_of_range"
        assert "Glucose" in results[0].evidence
        assert "ALT" in results[0].evidence
        # Should mention both results
        assert "2 result" in results[0].evidence or "2 results" in results[0].evidence
    finally:
        import os
        os.unlink(db)


# --- 8. Missing table → no findings, no crash ---
def test_missing_table_no_crash():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        con = sqlite3.connect(tmp.name)
        con.close()
        results = labs_findings(tmp.name, "2031-07-10")
        assert results == []
    finally:
        import os
        os.unlink(tmp.name)


# --- 9. Missing file → no findings, no crash ---
def test_missing_file_no_crash():
    results = labs_findings("/nonexistent/path/labs.sqlite", "2031-07-10")
    assert results == []


# --- 10. ref_date as date object works too ---
def test_ref_date_date_object():
    db = _make_db([("o1", "Glucose", _date(10), 145.0, "mg/dL", 70.0, 99.0, "HIGH")])
    try:
        results = labs_findings(db, date(2031, 7, 10))
        assert len(results) == 1
        assert results[0].id == "lab_out_of_range"
    finally:
        import os
        os.unlink(db)
