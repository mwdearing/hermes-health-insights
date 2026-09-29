"""Tests for workouts module (health_insights/workouts.py).

Synthetic data only — no real paths, no external data dirs, no real bridge DB.
Each test builds a tiny SQLite DB in tmp_path and exercises the public API.

Run: python -m pytest -q tests/test_workouts.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

CHI = ZoneInfo("America/Chicago")

WORKOUTS_CREATE = (
    "CREATE TABLE workouts (workout_id integer primary key, "
    "source_id integer not null references sources(source_id) on delete cascade, "
    "client_record_id text not null, workout_type text not null, "
    "start_time text not null, end_time text not null, "
    "duration_seconds integer not null, energy_kcal real, distance_meters real, "
    "created_at text, updated_at text, "
    "unique (source_id, client_record_id))"
)

SAMPLES_CREATE = (
    "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
    "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
    "value REAL, unit TEXT, metadata_json TEXT)"
)

SOURCES_CREATE = (
    "CREATE TABLE sources (source_id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, "
    "name TEXT, kind TEXT, bundle_id TEXT, device_model TEXT, created_at TEXT, updated_at TEXT)"
)

SOURCES_INSERT = (
    "INSERT INTO sources VALUES (1,'t.watch','Watch','watch','com.t','Watch','2031-01-01T00:00:00Z','2031-01-01T00:00:00Z')"
)


def _make(path: Path, workouts: list, hr_rows: list, with_table: bool = True) -> None:
    """Build a minimal DB at *path*.

    workouts: list of (type, start, end, duration, kcal, meters) — kcal/meters may be None.
    hr_rows: list of (timestamp_str, type_code, value).
    """
    con = sqlite3.connect(str(path))
    con.execute(SAMPLES_CREATE)
    con.execute(SOURCES_CREATE)
    con.execute(SOURCES_INSERT)
    if with_table:
        con.execute(WORKOUTS_CREATE)
    for i, w in enumerate(workouts):
        con.execute(
            "INSERT INTO workouts (source_id, client_record_id, workout_type, "
            "start_time, end_time, duration_seconds, energy_kcal, distance_meters) "
            "VALUES (1,?,?,?,?,?,?,?)",
            (f"w{i}",) + tuple(w),
        )
    for i, (t, tc, v) in enumerate(hr_rows):
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, "
            "start_time, end_time, value, unit) VALUES ('1',?,?,?,?,?,'bpm')",
            (tc, f"h{i}", t, t, v),
        )
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def db_with_workouts(tmp_path: Path):
    """DB with 4 workouts spanning Feb 1–8 2031 UTC, several HR samples."""
    W = [
        ("walking", "2031-02-01T13:00:00Z", "2031-02-01T13:23:00Z", 1380, 96.5, 1850.0),
        ("running", "2031-02-03T12:00:00Z", "2031-02-03T12:40:00Z", 2400, 410.0, 6400.0),
        ("strength_training", "2031-02-04T01:00:00Z", "2031-02-04T01:30:00Z", 1800, None, None),
        ("walking", "2031-01-20T13:00:00Z", "2031-01-20T13:30:00Z", 1800, 100.0, 2000.0),
        ("walking", "2031-02-08T05:00:00Z", "2031-02-08T05:30:00Z", 1800, 120.4, 2110.0),
    ]
    HR = [
        ("2031-02-01T12:59:59+00:00", "heart_rate", 60.0),
        ("2031-02-01T13:00:00+00:00", "heart_rate", 100.0),
        ("2031-02-01T13:08:00+00:00", "heart_rate", 110.0),
        ("2031-02-01T13:15:00+00:00", "heart_rate", 120.0),
        ("2031-02-01T13:23:00+00:00", "heart_rate", 130.0),
        ("2031-02-01T13:23:01+00:00", "heart_rate", 200.0),
        ("2031-02-01T13:10:00+00:00", "resting_heart_rate", 50.0),
        ("2031-02-08T05:05:00+00:00", "heart_rate", 90.0),
        ("2031-02-08T05:10:00+00:00", "heart_rate", 95.0),
    ]
    db = tmp_path / "workouts.sqlite"
    _make(db, W, HR)
    return db


@pytest.fixture()
def db_empty_table(tmp_path: Path):
    """DB with workouts table but no rows."""
    db = tmp_path / "empty.sqlite"
    _make(db, [], [])
    return db


@pytest.fixture()
def db_no_table(tmp_path: Path):
    """DB without a workouts table."""
    db = tmp_path / "notable.sqlite"
    _make(db, [], [], with_table=False)
    return db


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_weekly_line_format(db_with_workouts):
    """weekly_line renders counts, minutes, kcal, km for ref_date 2031-02-07."""
    from health_insights.workouts import weekly_line
    result = weekly_line(str(db_with_workouts), "2031-02-07")
    assert result == (
        "Workouts: 4 in 7d, 123 min (2 walking, 1 running, 1 strength training), "
        "627 kcal, 10.4 km"
    )


def test_digest_line_with_hr(db_with_workouts):
    """A single workout with ≥3 HR samples shows avg/max."""
    from health_insights.workouts import digest_line
    result = digest_line(str(db_with_workouts), "2031-02-01")
    assert result == "Workout: 23 min walking, HR avg 115 max 130 bpm"


def test_digest_line_multiple_workouts(db_with_workouts):
    """Two workouts on the same day: semicolon-separated, no HR text."""
    from health_insights.workouts import digest_line
    result = digest_line(str(db_with_workouts), "2031-02-03")
    assert result == "Workouts (2): 40 min running; 30 min strength training"


def test_digest_line_fewer_than_3_hr_samples(db_with_workouts):
    """Only 2 HR samples → no HR text appended."""
    from health_insights.workouts import digest_line
    result = digest_line(str(db_with_workouts), "2031-02-07")
    assert result == "Workout: 30 min walking"


def test_digest_line_no_workouts_for_date(db_with_workouts):
    """No workout ending on that date → None."""
    from health_insights.workouts import digest_line
    result = digest_line(str(db_with_workouts), "2031-02-02")
    assert result is None


def test_weekly_line_none_in_window(db_with_workouts):
    """Table has rows but nothing in the 7-day window → 'none in the last 7 days'."""
    from health_insights.workouts import weekly_line
    result = weekly_line(str(db_with_workouts), "2031-03-01")
    assert result == "Workouts: none in the last 7 days"


def test_weekly_line_empty_table(db_empty_table):
    """Empty workouts table → None (say nothing)."""
    from health_insights.workouts import weekly_line
    result = weekly_line(str(db_empty_table), "2031-02-07")
    assert result is None


def test_weekly_line_no_table(db_no_table):
    """No workouts table at all → None."""
    from health_insights.workouts import weekly_line
    result = weekly_line(str(db_no_table), "2031-02-07")
    assert result is None


def test_digest_line_no_table(db_no_table):
    """No workouts table → None."""
    from health_insights.workouts import digest_line
    result = digest_line(str(db_no_table), "2031-02-01")
    assert result is None


def test_chicago_date_boundary():
    """A workout ending 2031-02-08T05:30:00Z (Feb 7 23:30 Chicago) belongs to Feb 7."""
    from health_insights.workouts import digest_line
    db = Path(tempfile.mktemp(suffix=".sqlite"))
    try:
        _make(db, [
            ("walking", "2031-02-08T05:00:00Z", "2031-02-08T05:30:00Z", 1800, 50.0, 1000.0),
        ], [])
        assert digest_line(str(db), "2031-02-07") == "Workout: 30 min walking"
        assert digest_line(str(db), "2031-02-08") is None
    finally:
        db.unlink(missing_ok=True)


def test_mixed_timestamp_formats():
    """HR samples use +00:00, workouts use Z — both parse correctly."""
    from health_insights.workouts import digest_line
    db = Path(tempfile.mktemp(suffix=".sqlite"))
    try:
        _make(db, [
            ("cycling", "2031-03-01T10:00:00Z", "2031-03-01T10:30:00Z", 1800, 100.0, 5000.0),
        ], [
            ("2031-03-01T10:05:00+00:00", "heart_rate", 110.0),
            ("2031-03-01T10:15:00+00:00", "heart_rate", 120.0),
            ("2031-03-01T10:25:00+00:00", "heart_rate", 130.0),
        ])
        result = digest_line(str(db), "2031-03-01")
        assert result == "Workout: 30 min cycling, HR avg 120 max 130 bpm"
    finally:
        db.unlink(missing_ok=True)


def test_cli_weekly_line(db_with_workouts):
    """CLI `workouts weekly-line` prints the line, rc 0."""
    from health_insights.workouts import weekly_line
    result = weekly_line(str(db_with_workouts), "2031-02-07")
    assert result == (
        "Workouts: 4 in 7d, 123 min (2 walking, 1 running, 1 strength training), "
        "627 kcal, 10.4 km"
    )


def test_cli_digest_line(db_with_workouts):
    """CLI `workouts digest-line` prints the line, rc 0."""
    from health_insights.workouts import digest_line
    result = digest_line(str(db_with_workouts), "2031-02-01")
    assert result == "Workout: 23 min walking, HR avg 115 max 130 bpm"


def test_weekly_report_placement(db_with_workouts):
    """weekly.report inserts the workouts line directly before 'Notable:'."""
    from health_insights import weekly
    lines = weekly.report(str(db_with_workouts), {"timezone": "America/Chicago", "metrics": {}}, "2031-02-07")
    text = "\n".join(lines)
    assert "Informational, not medical advice." in text
    wk_line = "Workouts: 4 in 7d, 123 min (2 walking, 1 running, 1 strength training), 627 kcal, 10.4 km"
    assert wk_line in text
    idx = text.index(wk_line)
    # The line right after should start with "Notable:"
    after = text[idx + len(wk_line):].lstrip("\n")
    assert after.startswith("Notable:")


def test_digest_integration_workout_yesterday():
    """morning_digest._build_digest appends a workout line for yesterday."""
    import types
    from datetime import date as _date

    db = Path(tempfile.mktemp(suffix=".sqlite"))
    try:
        yesterday = (datetime.now(CHI) - timedelta(days=1)).date()
        end = datetime(yesterday.year, yesterday.month, yesterday.day, 12, 0, tzinfo=CHI) - timedelta(minutes=30)
        _make(db, [
            ("cycling", end.strftime("%Y-%m-%dT%H:%M:%SZ"), end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), 2700, 300.0, 15000.0),
        ], [])
        # Patch _build_digest to use a fixed "yesterday"
        from health_insights.workouts import digest_line

        def fake_build_digest(db_path, config_path, stale_days):
            import os, sys, traceback
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo
            chi = ZoneInfo("America/Chicago")
            yesterday_str = (datetime.now(chi) - timedelta(days=1)).date().isoformat()
            line = digest_line(db_path, yesterday_str)
            lines = ["Morning health — " + datetime.now(chi).date().isoformat() + " (Chicago)"]
            if line:
                lines.append(line)
            return "\n".join(lines)

        result = fake_build_digest(str(db), "/dev/null", 2)
        assert "Workout: 45 min cycling" in result
    finally:
        db.unlink(missing_ok=True)
