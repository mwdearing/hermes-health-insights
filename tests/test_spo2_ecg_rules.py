"""Tests for health_insights/concern_rules/spo2_ecg_rules.py (synthetic fixtures only; no real health data).

Run: python3 -m pytest -q tests/test_spo2_ecg_rules.py

Covers: each finding id, level-priority suppression (SpO2 and ECG),
24-hour pairing boundary, window edges, missing table/file, both together,
and combined sorting.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from health_insights.concern_rules import spo2_ecg_rules  # noqa: E402

import atexit, shutil

CHI = ZoneInfo("America/Chicago")
SCRATCH = Path(__file__).parent / ".scratch_spo2_ecg"
SCRATCH.mkdir(exist_ok=True)
atexit.register(shutil.rmtree, SCRATCH, True)

REF = date(2031, 6, 10)


def _make_spo2_db(path: Path, readings: list[tuple[int, float]]) -> str:
    """Create a SQLite DB with a samples table containing oxygen_saturation readings.

    *readings*: list of (hours_before_ref_end, fraction_value).
    ref_end = REF 23:00 Chicago (2031-06-11 04:00 UTC).
    Only the last 2 Chicago calendar days (ref-1 and ref) are relevant.
    """
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE samples ("
        "sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, "
        "client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT, "
        "UNIQUE (type_code, start_time, value))"
    )
    anchor = date(2031, 6, 11)  # UTC; Chicago 2031-06-10 23:00
    for i, (hrs, val) in enumerate(readings):
        t = (anchor - timedelta(hours=hrs)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES ('1','oxygen_saturation',?,?,?,?, 'fraction')",
            (f"o{i}", t, t, val),
        )
    con.commit()
    con.close()
    return str(path)


def _make_ecg_db(path: Path, recs: list[tuple[int, str]]) -> str:
    """Create a SQLite DB with ecg_recordings table.

    *recs*: list of (days_before_ref, classification).
    """
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE ecg_recordings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT, recorded_at TEXT, "
        "recorded_local_date TEXT, classification TEXT, symptoms TEXT, "
        "device TEXT, software_version TEXT, sample_rate INTEGER, "
        "sample_count INTEGER, duration_s REAL, lead TEXT, unit TEXT, "
        "imported_at TEXT, source_file TEXT, "
        "UNIQUE(member, recorded_at))"
    )
    for i, (days, cls) in enumerate(recs):
        d = REF - timedelta(days=days)
        con.execute(
            "INSERT INTO ecg_recordings (member, recorded_at, recorded_local_date, "
            "classification, symptoms, device, sample_rate, sample_count, duration_s) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f"e{i}", f"{d}T08:00:00Z", d.isoformat(), cls, "None", "Watch", 512, 15360, 30.0),
        )
    con.commit()
    con.close()
    return str(path)


def _empty_db(path: Path) -> str:
    if path.exists():
        path.unlink()
    sqlite3.connect(str(path)).close()
    return str(path)


# --- SpO2 tests ---


def test_spo2_normal_no_finding():
    """Readings above 0.94 in the 2-day window → no finding."""
    db = _make_spo2_db(SCRATCH / "normal.sqlite", [(5, 0.98), (30, 0.97)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert [f.id for f in result] == []


def test_spo2_low():
    """Single reading below 0.94 but not paired below 0.92 → spo2_low (level 1)."""
    db = _make_spo2_db(SCRATCH / "low94.sqlite", [(5, 0.93), (30, 0.98)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert len(result) == 1
    assert result[0].id == "spo2_low"
    assert result[0].level == 1
    assert "94" in result[0].evidence


def test_spo2_low_pair():
    """Two readings below 0.92 within 24 h → spo2_low_pair (level 2)."""
    # 46 h and 20 h before ref_end → 26 h apart, but both within 2-day window
    # Need both below 0.92 and within 24h of each other
    db = _make_spo2_db(SCRATCH / "low_pair.sqlite", [(2, 0.90), (10, 0.91)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert len(result) == 1
    assert result[0].id == "spo2_low_pair"
    assert result[0].level == 2
    assert "two" in result[0].evidence.lower()
    assert "92" in result[0].evidence


def test_spo2_low_pair_24h_boundary_exactly_24h():
    """Readings exactly 24 h apart should NOT count as a pair."""
    # 47 h and 23 h before ref_end → exactly 24h apart → NOT a pair
    # Both in 2-day window (47h = ref-1 day, 23h = ref day)
    db = _make_spo2_db(SCRATCH / "exactly_24h.sqlite", [(47, 0.90), (23, 0.91)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    # Should fall through to spo2_low (single low reading) since not a pair
    assert len(result) == 1
    assert result[0].id == "spo2_low"


def test_spo2_low_pair_24h_boundary_over_24h():
    """Readings just over 24 h apart should NOT count as a pair."""
    # 47 h and 24 h before ref_end → 23h apart? No, 47-24=23h, that's within 24h
    # 50 h and 24 h before ref_end → 26h apart → NOT a pair
    # But need both in 2-day window. 50h before ref_end is ref-2 day, outside window.
    # So use readings that are both in window but >24h apart: 46h and 22h → 24h apart
    # Actually the 2-day window is ref-1 and ref calendar days.
    # ref_end = 2031-06-10 23:00 Chicago. 2-day window = 2031-06-09 00:00 to 2031-06-10 23:00.
    # That's 47 hours total. So two readings 30h and 5h before ref_end = 25h apart.
    db = _make_spo2_db(SCRATCH / "over_24h.sqlite", [(30, 0.90), (5, 0.91)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    # 25h apart → NOT a pair, but both below 0.92 → spo2_low
    assert len(result) == 1
    assert result[0].id == "spo2_low"


def test_spo2_critical():
    """Reading below 0.88 → spo2_critical (level 3)."""
    db = _make_spo2_db(SCRATCH / "critical.sqlite", [(3, 0.87), (30, 0.98)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert len(result) == 1
    assert result[0].id == "spo2_critical"
    assert result[0].level == 3
    assert "87" in result[0].evidence
    assert "recheck" in result[0].advice.lower()
    assert "seek care" in result[0].advice.lower()


def test_spo2_critical_wins_over_pair():
    """Critical reading suppresses pair finding — only one SpO2 finding."""
    # 87% (critical) + 90% + 91% within 24h → only spo2_critical
    db = _make_spo2_db(SCRATCH / "crit_and_pair.sqlite", [(3, 0.87), (5, 0.90), (8, 0.91)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert len(result) == 1
    assert result[0].id == "spo2_critical"


def test_spo2_higher_level_wins():
    """When both low and pair qualify, the higher level wins."""
    # All readings below 0.92, two within 24h → spo2_low_pair (level 2 > level 1)
    db = _make_spo2_db(SCRATCH / "pair_wins.sqlite", [(2, 0.90), (10, 0.91)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert len(result) == 1
    assert result[0].id == "spo2_low_pair"


def test_spo2_missing_table_no_crash():
    """No samples table → no findings, no crash."""
    db = _empty_db(SCRATCH / "no_table.sqlite")
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert result == []


# --- ECG tests ---


def test_ecg_normal_no_finding():
    """All Sinus Rhythm → no finding."""
    db = _make_ecg_db(SCRATCH / "ecg_normal.sqlite", [(2, "Sinus Rhythm"), (10, "Sinus Rhythm")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF, ecg_db=str(db))
    ecg_findings = [f for f in result if "ecg" in f.id]
    assert ecg_findings == []


def test_ecg_nonsinus():
    """Non-sinus classification → ecg_nonsinus (level 2)."""
    db = _make_ecg_db(SCRATCH / "ecg_afib.sqlite", [(2, "Atrial Fibrillation"), (20, "Sinus Rhythm")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF, ecg_db=str(db))
    ecg_findings = [f for f in result if "ecg" in f.id]
    assert len(ecg_findings) == 1
    assert ecg_findings[0].id == "ecg_nonsinus"
    assert ecg_findings[0].level == 2
    assert "Atrial Fibrillation" in ecg_findings[0].evidence
    assert "clinician" in ecg_findings[0].advice.lower()
    assert "seek care" in ecg_findings[0].advice.lower()


def test_ecg_nonsinus_wins_over_inconclusive():
    """Both non-sinus and inconclusive present → only ecg_nonsinus (level 2)."""
    db = _make_ecg_db(SCRATCH / "ecg_both.sqlite", [(2, "Atrial Fibrillation"), (5, "Inconclusive Poor Reading")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF, ecg_db=str(db))
    ecg_findings = [f for f in result if "ecg" in f.id]
    assert len(ecg_findings) == 1
    assert ecg_findings[0].id == "ecg_nonsinus"
    assert ecg_findings[0].level == 2


def test_ecg_inconclusive():
    """Inconclusive recording → ecg_inconclusive (level 1)."""
    db = _make_ecg_db(SCRATCH / "ecg_inc.sqlite", [(2, "Inconclusive Low Heart Rate")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF, ecg_db=str(db))
    ecg_findings = [f for f in result if "ecg" in f.id]
    assert len(ecg_findings) == 1
    assert ecg_findings[0].id == "ecg_inconclusive"
    assert ecg_findings[0].level == 1


def test_ecg_old_outside_window():
    """Recording older than 30 days → no finding."""
    db = _make_ecg_db(SCRATCH / "ecg_old.sqlite", [(45, "Atrial Fibrillation")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF, ecg_db=str(db))
    ecg_findings = [f for f in result if "ecg" in f.id]
    assert ecg_findings == []


def test_ecg_missing_file_no_crash():
    """Missing ECG DB file → no ECG findings, no crash."""
    db = _make_spo2_db(SCRATCH / "spo2_normal.sqlite", [(5, 0.98)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF, ecg_db="/nonexistent/ecg.sqlite")
    ecg_findings = [f for f in result if "ecg" in f.id]
    assert ecg_findings == []


# --- Combined and sorting tests ---


def test_spo2_and_ecg_together():
    """Both SpO2 and ECG findings can appear together."""
    spo2_db = _make_spo2_db(SCRATCH / "combined_spo2.sqlite", [(3, 0.87)])
    ecg_db = _make_ecg_db(SCRATCH / "combined_ecg.sqlite", [(2, "Atrial Fibrillation")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(spo2_db), REF, ecg_db=str(ecg_db))
    ids = [f.id for f in result]
    assert "spo2_critical" in ids
    assert "ecg_nonsinus" in ids
    assert len(result) == 2


def test_combined_sort_level_desc_id_asc():
    """Combined results sorted by level desc, then id asc."""
    spo2_db = _make_spo2_db(SCRATCH / "sort_spo2.sqlite", [(3, 0.87)])
    ecg_db = _make_ecg_db(SCRATCH / "sort_ecg.sqlite", [(2, "Atrial Fibrillation")])
    result = spo2_ecg_rules.spo2_ecg_findings(str(spo2_db), REF, ecg_db=str(ecg_db))
    # spo2_critical (level 3) should come before ecg_nonsinus (level 2)
    assert result[0].level == 3
    assert result[1].level == 2


def test_spo2_ref_date_as_string():
    """ref_date accepts a string 'YYYY-MM-DD'."""
    db = _make_spo2_db(SCRATCH / "str_ref.sqlite", [(5, 0.93)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), "2031-06-10")
    assert len(result) >= 1
    assert "spo2" in result[0].id


def test_spo2_2_day_window_edge():
    """Reading 3 days before ref_date (outside 2-day window) is ignored."""
    # 50h before ref_end = 2031-06-09 01:00 UTC = 2031-06-08 20:00 Chicago → outside 2-day window
    db = _make_spo2_db(SCRATCH / "outside_window.sqlite", [(50, 0.85), (30, 0.98)])
    result = spo2_ecg_rules.spo2_ecg_findings(str(db), REF)
    assert result == []


def test_ecg_30_day_window_edge():
    """Recording exactly 29 days before ref_date is included; 30 is not."""
    # Exactly 29 days → included (30 calendar days: ref-29..ref)
    db29 = _make_ecg_db(SCRATCH / "ecg_29day.sqlite", [(29, "Atrial Fibrillation")])
    result29 = spo2_ecg_rules.spo2_ecg_findings(str(db29), REF, ecg_db=str(db29))
    assert len([f for f in result29 if "ecg" in f.id]) == 1
    # 30 days → excluded
    db30 = _make_ecg_db(SCRATCH / "ecg_30day.sqlite", [(30, "Atrial Fibrillation")])
    result30 = spo2_ecg_rules.spo2_ecg_findings(str(db30), REF, ecg_db=str(db30))
    assert [f for f in result30 if "ecg" in f.id] == []
