"""Tests for health_insights.concerns and health_insights.concern_rules.bp_rules.

Synthetic data only — no real paths, no external datasets, no user home paths.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date as Date, datetime, timedelta

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCR = os.path.join(REPO, ".scratch-w3-tests")


@pytest.fixture(scope="module", autouse=True)
def scratch_dir():
    """Create a shared scratch directory for all tests."""
    os.makedirs(SCR, exist_ok=True)
    yield
    # No cleanup needed — module-scoped, files are synthetic.


def _make_db(readings: list[tuple[str, float, float]]) -> str:
    """Create a synthetic bridge DB with paired BP readings.

    Each reading is (start_time_iso_utc, systolic, diastolic).
    Chicago is UTC-5 from 2031-03-10 (DST).
    """
    path = os.path.join(SCR, f"db_{len(readings)}.sqlite")
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE samples ("
        "sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, "
        "client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT, "
        "UNIQUE (type_code, start_time, value)"
        ")"
    )
    for i, (t, sys_v, dia_v) in enumerate(readings):
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES ('1','blood_pressure_systolic',?,?,?,?, 'mmHg')",
            (f"s{i}", t, t, sys_v),
        )
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES ('1','blood_pressure_diastolic',?,?,?,?, 'mmHg')",
            (f"d{i}", t, t, dia_v),
        )
    con.commit()
    con.close()
    return path


def _make_no_samples_db() -> str:
    """Create a DB with no samples table."""
    path = os.path.join(SCR, "db_nosamples.sqlite")
    sqlite3.connect(path).close()
    return path


# ---------------------------------------------------------------------------
# 1. Finding dataclass
# ---------------------------------------------------------------------------
def test_finding_dataclass():
    from health_insights.concerns import Finding

    f = Finding(id="test_1", level=2, title="High BP", evidence="186/124", source="AHA", advice="Seek care")
    assert f.id == "test_1"
    assert f.level == 2
    d = f.to_dict()
    assert isinstance(d, dict)
    assert d["id"] == "test_1"
    assert d["level"] == 2
    assert d["title"] == "High BP"
    assert d["evidence"] == "186/124"
    assert d["source"] == "AHA"
    assert d["advice"] == "Seek care"


# ---------------------------------------------------------------------------
# 2. LEVEL_ICONS
# ---------------------------------------------------------------------------
def test_level_icons():
    from health_insights.concerns import LEVEL_ICONS

    assert LEVEL_ICONS == {0: "✅", 1: "👀", 2: "⚠️", 3: "🚨"}


# ---------------------------------------------------------------------------
# 3. worst_level
# ---------------------------------------------------------------------------
def test_worst_level_empty():
    from health_insights.concerns import worst_level

    assert worst_level([]) == 0


def test_worst_level_max():
    from health_insights.concerns import Finding, worst_level

    findings = [
        Finding(id="a", level=1, title="", evidence="", source="", advice=""),
        Finding(id="b", level=3, title="", evidence="", source="", advice=""),
        Finding(id="c", level=2, title="", evidence="", source="", advice=""),
    ]
    assert worst_level(findings) == 3


# ---------------------------------------------------------------------------
# 4. DEFAULT_RULES contains bp
# ---------------------------------------------------------------------------
def test_default_rules_contains_bp():
    from health_insights.concerns import DEFAULT_RULES

    names = [n for n, _ in DEFAULT_RULES]
    assert "bp" in names


# ---------------------------------------------------------------------------
# 5. evaluate — severe reading (level 3)
# ---------------------------------------------------------------------------
def test_severe_reading():
    """A severe reading (186/124) on the reference day should produce a level-3 finding."""
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
        ("2031-03-15T02:00:00Z", 186, 124),  # Chicago Mar 14 21:00
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    ids = [f.id for f in results]
    assert "bp_severe" in ids
    severe = next(f for f in results if f.id == "bp_severe")
    assert severe.level == 3
    assert "186/124" in severe.evidence
    assert "recheck" in severe.advice.lower()
    assert "seek care" in severe.advice.lower()
    assert "AHA" in severe.source


# ---------------------------------------------------------------------------
# 6. evaluate — severe reading 3 days ago (outside 2-day window)
# ---------------------------------------------------------------------------
def test_severe_old_no_finding():
    """A severe reading 3 days before ref should NOT trigger bp_severe."""
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
        ("2031-03-12T02:00:00Z", 186, 124),  # Chicago Mar 11 20:00 — 3 days before ref
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    ids = [f.id for f in results]
    assert "bp_severe" not in ids


# ---------------------------------------------------------------------------
# 7. evaluate — Stage 2 average (level 2)
# ---------------------------------------------------------------------------
def test_stage2_avg():
    """7-day average in Stage 2 should produce level-2 finding."""
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-09T16:00:00Z", 144, 91),
        ("2031-03-10T16:00:00Z", 146, 91),
        ("2031-03-11T16:00:00Z", 148, 91),
        ("2031-03-12T16:00:00Z", 146, 91),
        ("2031-03-13T16:00:00Z", 145, 91),
        ("2031-03-14T16:00:00Z", 147, 91),
        ("2031-03-16T03:30:00Z", 146, 91),  # Chicago Mar 15 22:30 — inside window
        ("2031-03-09T04:00:00Z", 170, 100),  # Chicago Mar 8 22:00 — outside window
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    ids = [f.id for f in results]
    assert "bp_stage2_avg" in ids
    stage2 = next(f for f in results if f.id == "bp_stage2_avg")
    assert stage2.level == 2
    assert "146/91" in stage2.evidence
    assert "7 readings" in stage2.evidence
    assert "clinician" in stage2.advice.lower()
    # Stage 2 should NOT also emit Stage 1
    assert "bp_stage1_avg" not in ids


# ---------------------------------------------------------------------------
# 8. evaluate — Stage 1 persistent (prior week also Stage 1/2)
# ---------------------------------------------------------------------------
def test_stage1_persistent():
    """Stage 1 average with prior week also Stage 1/2 should produce level-1."""
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-10T16:00:00Z", 134, 84),
        ("2031-03-11T16:00:00Z", 134, 84),
        ("2031-03-13T16:00:00Z", 134, 84),
        ("2031-03-14T16:00:00Z", 134, 84),
        ("2031-03-15T16:00:00Z", 134, 84),
        # Prior week: Mar 3-6, also Stage 1
        ("2031-03-03T16:00:00Z", 135, 85),
        ("2031-03-04T16:00:00Z", 135, 85),
        ("2031-03-05T16:00:00Z", 135, 85),
        ("2031-03-06T16:00:00Z", 135, 85),
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    ids = [f.id for f in results]
    assert "bp_stage1_avg" in ids
    stage1 = next(f for f in results if f.id == "bp_stage1_avg")
    assert stage1.level == 1
    assert "134/84" in stage1.evidence


# ---------------------------------------------------------------------------
# 9. evaluate — Stage 1 new (prior week normal) → no finding
# ---------------------------------------------------------------------------
def test_stage1_new_no_finding():
    """Stage 1 average but prior week is normal → no Stage 1 finding."""
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-10T16:00:00Z", 134, 84),
        ("2031-03-11T16:00:00Z", 134, 84),
        ("2031-03-13T16:00:00Z", 134, 84),
        # Prior week: normal
        ("2031-03-03T16:00:00Z", 118, 76),
        ("2031-03-04T16:00:00Z", 118, 76),
        ("2031-03-05T16:00:00Z", 118, 76),
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    ids = [f.id for f in results]
    assert "bp_stage1_avg" not in ids


# ---------------------------------------------------------------------------
# 10. evaluate — normal readings → no findings
# ---------------------------------------------------------------------------
def test_normal_no_findings():
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 11. evaluate — too few readings (2) → no findings
# ---------------------------------------------------------------------------
def test_too_few_readings():
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-14T16:00:00Z", 150, 95),
        ("2031-03-15T16:00:00Z", 150, 95),
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 12. evaluate — empty samples → no findings
# ---------------------------------------------------------------------------
def test_empty_samples():
    from health_insights.concerns import evaluate

    db = _make_db([])
    results = evaluate(db, "2031-03-15")
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 13. evaluate — no samples table → no crash
# ---------------------------------------------------------------------------
def test_no_samples_table():
    from health_insights.concerns import evaluate

    db = _make_no_samples_db()
    results = evaluate(db, "2031-03-15")
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 14. evaluate — rule isolation (raising rule becomes error finding)
# ---------------------------------------------------------------------------
def test_rule_isolation():
    from health_insights.concerns import evaluate, Finding

    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
    ]
    db = _make_db(readings)

    def bad_rule(db_path, ref_date):
        raise RuntimeError("test boom")

    def good_rule(db_path, ref_date):
        return [Finding(id="good_finding", level=2, title="G", evidence="e", source="s", advice="a")]

    results = evaluate(db, "2031-03-15", rules=[("bad", bad_rule), ("good", good_rule)])
    ids = [f.id for f in results]
    assert "bad_error" in ids
    assert "good_finding" in ids
    err_finding = next(f for f in results if f.id == "bad_error")
    assert err_finding.level == 1
    assert "RuntimeError" in err_finding.evidence
    assert "Traceback" not in err_finding.evidence


# ---------------------------------------------------------------------------
# 15. evaluate — ordering (level desc, then id asc)
# ---------------------------------------------------------------------------
def test_ordering():
    from health_insights.concerns import evaluate, Finding

    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
    ]
    db = _make_db(readings)

    def ordered_rule(db_path, ref_date):
        return [
            Finding(id="z_low", level=1, title="", evidence="", source="", advice=""),
            Finding(id="a_low", level=1, title="", evidence="", source="", advice=""),
            Finding(id="m_hi", level=3, title="", evidence="", source="", advice=""),
        ]

    results = evaluate(db, "2031-03-15", rules=[("ord", ordered_rule)])
    ids = [f.id for f in results]
    assert ids == ["m_hi", "a_low", "z_low"]


# ---------------------------------------------------------------------------
# 16. evaluate — UTC date differs from Chicago date
# ---------------------------------------------------------------------------
def test_utc_chicago_date_boundary():
    """A reading at 2031-03-16T03:30:00Z is Chicago 2031-03-15 22:30 — should be inside the window."""
    from health_insights.concerns import evaluate

    readings = [
        ("2031-03-09T16:00:00Z", 144, 91),
        ("2031-03-10T16:00:00Z", 146, 91),
        ("2031-03-11T16:00:00Z", 148, 91),
        ("2031-03-12T16:00:00Z", 146, 91),
        ("2031-03-13T16:00:00Z", 145, 91),
        ("2031-03-14T16:00:00Z", 147, 91),
        ("2031-03-16T03:30:00Z", 146, 91),  # Chicago Mar 15
    ]
    db = _make_db(readings)
    results = evaluate(db, "2031-03-15")
    ids = [f.id for f in results]
    # 7 readings in the Chicago window, avg should be Stage 2
    assert "bp_stage2_avg" in ids


# ---------------------------------------------------------------------------
# 17. CLI — plain output with findings
# ---------------------------------------------------------------------------
def test_cli_plain_with_findings():
    readings = [
        ("2031-03-09T16:00:00Z", 144, 91),
        ("2031-03-10T16:00:00Z", 146, 91),
        ("2031-03-11T16:00:00Z", 148, 91),
        ("2031-03-12T16:00:00Z", 146, 91),
        ("2031-03-13T16:00:00Z", 145, 91),
        ("2031-03-14T16:00:00Z", 147, 91),
        ("2031-03-16T03:30:00Z", 146, 91),
    ]
    db = _make_db(readings)
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO
    r = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "concerns", "--db", db, "--date", "2031-03-15"],
        capture_output=True, text=True, cwd=REPO, timeout=30, env=env,
    )
    assert r.returncode == 0
    out = r.stdout.strip()
    assert out.startswith("⚠️")
    assert "146/91" in out


# ---------------------------------------------------------------------------
# 18. CLI — plain output no findings
# ---------------------------------------------------------------------------
def test_cli_plain_no_findings():
    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
    ]
    db = _make_db(readings)
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO
    r = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "concerns", "--db", db, "--date", "2031-03-15"],
        capture_output=True, text=True, cwd=REPO, timeout=30, env=env,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "✅ No concerns found"


# ---------------------------------------------------------------------------
# 19. CLI — JSON output
# ---------------------------------------------------------------------------
def test_cli_json_output():
    readings = [
        ("2031-03-14T15:00:00Z", 112, 72),
        ("2031-03-13T15:00:00Z", 114, 74),
        ("2031-03-12T15:00:00Z", 116, 75),
        ("2031-03-15T02:00:00Z", 186, 124),
    ]
    db = _make_db(readings)
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO
    r = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "concerns", "--db", db, "--date", "2031-03-15", "--json"],
        capture_output=True, text=True, cwd=REPO, timeout=30, env=env,
    )
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert isinstance(data, list)
    assert len(data) > 0
    assert set(data[0]).issuperset({"id", "level", "title", "evidence", "source", "advice"})
    assert data[0]["id"] == "bp_severe"
