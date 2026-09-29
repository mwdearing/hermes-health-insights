"""Tests for health_insights/bp.py (synthetic fixtures only; no real health data).

Run: python3 -m pytest -q tests/test_bp.py

Covers: every category boundary in the band table, pairing/unpairing, the
3-reading rule for the 7-day average, RISING window rules, NO_READING_4D,
Chicago day grouping, and report line format.
"""
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from health_insights import bp  # noqa: E402

import atexit, shutil
SCRATCH = Path(__file__).parent / ".scratch_bp"
SCRATCH.mkdir(exist_ok=True)
atexit.register(shutil.rmtree, SCRATCH, True)

CHI = ZoneInfo("America/Chicago")
TODAY = date(2030, 3, 15)


def _make_db(path: Path, readings: list[tuple[str, str, int, int]]) -> str:
    """readings: (YYYY-MM-DD, HH:MM, systolic, diastolic) in local time."""
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, value REAL, unit TEXT, metadata_json TEXT)")
    n = 0
    for d, hm, s, di in readings:
        for code, v in (("blood_pressure_systolic", s), ("blood_pressure_diastolic", di)):
            loc = datetime.fromisoformat(f"{d}T{hm}:00").replace(tzinfo=CHI)
            ts = loc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            con.execute("INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) VALUES ('t', ?, ?, ?, ?, ?, 'mmHg')", (code, f"r{n}", ts, ts, float(v)))
            n += 1
    con.commit()
    con.close()
    return str(path)


def _run(db_path: str, ref_date: date = TODAY) -> str:
    return "\n".join(bp.report(db_path, ref_date.isoformat()))


def _insert_extra(db_path: str, code: str, hm: str, value: float) -> None:
    """Insert a lone systolic/diastolic sample at local time ``hm`` (no pairing)."""
    loc = datetime.fromisoformat(f"2030-03-14T{hm}:00").replace(tzinfo=CHI)
    ts = loc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
        "VALUES ('t', ?, ?, ?, ?, ?, 'mmHg')",
        (code, f"extra{code}", ts, ts, float(value)),
    )
    con.commit()
    con.close()


# --- category boundary table (step E) ---
@pytest.mark.parametrize("sys_val,dia_val,expected", [
    (129, 79, "Elevated"),
    (130, 79, "Stage 1"),
    (119, 80, "Stage 1"),
    (139, 89, "Stage 1"),
    (140, 89, "Stage 2"),
    (139, 90, "Stage 2"),
    (180, 100, "Stage 2"),
    (181, 100, "Severe"),
    (150, 120, "Stage 2"),
    (150, 121, "Severe"),
    (119, 79, "Normal"),
])
def test_category_boundaries(sys_val, dia_val, expected):
    assert bp.category(sys_val, dia_val) == expected


def test_category_systolic_wins():
    # sys 119 (Normal) but dia 95 (Stage 2): higher band wins.
    assert bp.category(119, 95) == "Stage 2"


def test_category_dia_wins():
    # dia 79 (Normal) but sys 135 (Stage 1): higher band wins.
    assert bp.category(135, 79) == "Stage 1"


# --- pairing / unpairing ---
def test_unpaired_value_ignored():
    # A paired reading on 03-15, plus a lone systolic on 03-14 at a different
    # start_time that has no matching diastolic -> not a reading, so 03-14 is
    # ignored entirely.
    readings = [("2030-03-15", "08:00", 118, 76)]
    db_path = _make_db((SCRATCH / "tp_syst_only.sqlite"), readings)
    _insert_extra(db_path, "blood_pressure_systolic", "09:00", 190)
    out = _run(db_path)
    assert "BP flags: none" in out
    assert "190" not in out


# --- 3-reading rule ---
def test_insufficient_three_readings():
    readings = [("2030-03-14", "08:00", 118, 76), ("2030-03-15", "08:00", 120, 78)]
    out = _run(_make_db((SCRATCH / "tp_insufficient.sqlite"), readings))
    assert "BP 7d avg: insufficient (2 readings; need 3)" in out
    assert "Stage" not in out


def test_exactly_three_readings_runs():
    readings = [("2030-03-13", "08:00", 118, 76), ("2030-03-14", "08:00", 120, 78), ("2030-03-15", "08:00", 122, 80)]
    out = _run(_make_db((SCRATCH / "tp_three.sqlite"), readings))
    assert "BP 7d avg: 120/78 mmHg from 3 readings on 3 days" in out


# --- STAGE flags ---
def test_stage1_avg_flag():
    readings = [(f"2030-03-{d:02d}", "08:00", 134, 84) for d in range(11, 16)]
    out = _run(_make_db((SCRATCH / "tp_stage1.sqlite"), readings))
    assert "STAGE1_AVG" in out
    assert "Stage 1)" in out


def test_stage2_avg_flag():
    readings = [(f"2030-03-{d:02d}", "08:00", 146, 92) for d in range(11, 16)]
    out = _run(_make_db((SCRATCH / "tp_stage2.sqlite"), readings))
    assert "STAGE2_AVG" in out
    assert "Stage 2)" in out


def test_severe_reading_flag():
    readings = [(f"2030-03-{d:02d}", "08:00", 118, 76) for d in range(11, 15)]
    readings.append(("2030-03-15", "08:00", 185, 112))
    out = _run(_make_db((SCRATCH / "tp_severe.sqlite"), readings))
    assert "SEVERE_READING" in out
    assert "BP latest: 185/112 mmHg on Mar 15 (Severe)" in out


# --- NO_READING_4D ---
def test_no_reading_4d():
    readings = [(f"2030-03-{d:02d}", "08:00", 118, 76) for d in range(8, 12)]
    out = _run(_make_db((SCRATCH / "tp_noreading.sqlite"), readings))
    assert "BP flags: NO_READING_4D" in out


def test_no_reading_4d_absent_when_recent():
    readings = [(f"2030-03-{d:02d}", "08:00", 118, 76) for d in range(12, 16)]
    out = _run(_make_db((SCRATCH / "tp_recent.sqlite"), readings))
    assert "NO_READING_4D" not in out


# --- RISING window rules ---
def test_rising_flag():
    prior = [(f"2030-02-{d:02d}", "08:00", 118, 76) for d in range(20, 29)]
    recent = [(f"2030-03-{d:02d}", "08:00", 132, 80) for d in range(1, 16)]
    out = _run(_make_db((SCRATCH / "tp_rising.sqlite"), prior + recent))
    assert "RISING" in out
    assert "BP flags: STAGE1_AVG" in out


def test_rising_not_fired_when_steady():
    prior = [(f"2030-02-{d:02d}", "08:00", 119, 77) for d in range(20, 29)]
    recent = [(f"2030-03-{d:02d}", "08:00", 119, 77) for d in range(1, 16)]
    out = _run(_make_db((SCRATCH / "tp_steady.sqlite"), prior + recent))
    assert "BP flags: none" in out
    assert "RISING" not in out


def test_rising_requires_seven_each_window():
    # Only 6 readings in prior window (needs 7+), so RISING must not fire.
    # Use April (30 days) so the prior-window indices stay valid.
    prior = [(f"2030-04-{d:02d}", "08:00", 118, 76) for d in range(22, 29)]
    recent = [(f"2030-05-{d:02d}", "08:00", 135, 80) for d in range(1, 16)]
    out = _run(_make_db((SCRATCH / "tp_rising_short.sqlite"), prior + recent))
    assert "RISING" not in out


# --- Chicago day grouping ---
def test_chicago_day_grouping():
    # Prove grouping is by Chicago local date, not UTC. Reading 1 lands on
    # 2030-03-15 in Chicago (23:30 UTC = 18:30 CDT); reading 2 lands on
    # 2030-03-14 in Chicago (00:30 UTC = 19:30 CDT the previous day); reading 3
    # on 2030-03-13. Three distinct Chicago days -> "on 3 days".
    readings = [
        ("2030-03-15", "18:30", 120, 76),
        ("2030-03-14", "19:30", 122, 78),
        ("2030-03-13", "08:00", 116, 74),
    ]
    out = _run(_make_db((SCRATCH / "tp_chi.sqlite"), readings))
    assert "from 3 readings on 3 days" in out


def test_latest_uses_chicago_date():
    # Reading at 20:00 CDT 2030-03-14 labelled as Mar 14, not Mar 15.
    readings = [("2030-03-14", "20:00", 118, 76)]
    out = _run(_make_db((SCRATCH / "tp_latedate.sqlite"), readings))
    assert "BP latest: 118/76 mmHg on Mar 14" in out


# --- report format ---
def test_report_two_per_day():
    readings = [("2030-03-14", "08:00", 118, 76), ("2030-03-15", "08:00", 118, 76), ("2030-03-15", "20:00", 122, 78)]
    out = _run(_make_db((SCRATCH / "tp_twoperday.sqlite"), readings))
    assert "from 3 readings on 2 days" in out


def test_report_line_count():
    readings = [(f"2030-03-{d:02d}", "08:00", 112, 72) for d in range(11, 16)]
    out = _run(_make_db((SCRATCH / "tp_count.sqlite"), readings))
    assert len(out.splitlines()) <= 6


# --- SEVERE_READING freshness (review 2026-09-22): the flag must only reflect a recent reading, and a
# future-dated reading must never affect a report for an earlier reference date ---
def test_severe_reading_does_not_persist_beyond_the_freshness_window():
    """An old severe reading with nothing more recent must not keep flagging as current forever."""
    readings = [("2030-01-01", "08:00", 185, 112)]  # single severe reading, ~2.5 months before TODAY
    out = _run(_make_db((SCRATCH / "tp_stale_severe.sqlite"), readings))
    assert "SEVERE_READING" not in out
    assert "NO_READING_4D" in out  # consistent with the same freshness window


def test_severe_reading_present_within_the_freshness_window():
    """Sanity check: a severe reading still inside the window keeps flagging (unchanged behavior)."""
    readings = [(f"2030-03-{d:02d}", "08:00", 118, 76) for d in range(11, 15)]
    readings.append(("2030-03-15", "08:00", 185, 112))
    out = _run(_make_db((SCRATCH / "tp_fresh_severe.sqlite"), readings))
    assert "SEVERE_READING" in out


def test_future_dated_severe_reading_does_not_flag_an_earlier_report():
    """A reading dated after ref_date must never affect a report for ref_date (bad import / clock skew)."""
    readings = [("2030-03-15", "08:00", 118, 76), ("2030-03-20", "08:00", 185, 112)]  # future severe reading
    out = _run(_make_db((SCRATCH / "tp_future_severe.sqlite"), readings))
    assert "SEVERE_READING" not in out
    assert "BP latest: 118/76 mmHg on Mar 15 (Normal)" in out


def test_latest_line_says_how_old_a_stale_reading_is_and_agrees_with_the_flags():
    """Full review 2026-09-22: 'BP latest: ... (Severe)' from a 9-day-old reading next to 'flags: NO_READING_4D'
    read as a contradiction. The latest line now states the age when the reading is outside the 4-day window."""
    db = _make_db(SCRATCH / "stale_latest.sqlite", [((TODAY - timedelta(days=9)).isoformat(), "08:00", 185, 120)])
    out = _run(db)
    latest = [l for l in out.splitlines() if l.startswith("BP latest")][0]
    assert "Severe" in latest and "9 days ago" in latest, latest
    flags = [l for l in out.splitlines() if l.startswith("BP flags")][0]
    assert "NO_READING_4D" in flags and "SEVERE_READING" not in flags, flags
    fresh = _make_db(SCRATCH / "fresh_latest.sqlite", [((TODAY - timedelta(days=1)).isoformat(), "08:00", 185, 120)])
    latest = [l for l in _run(fresh).splitlines() if l.startswith("BP latest")][0]
    assert "days ago" not in latest, latest
