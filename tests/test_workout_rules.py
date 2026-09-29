"""Tests for workout concern rules.

Synthetic data only. No real paths, no real data directory, no real DB.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, timedelta

import pytest

from health_insights.concerns import Finding
from health_insights.concern_rules import workout_rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REF = date(2031, 8, 10)  # matches acceptance script


def _make_db(workouts: list[tuple[int, int, str]]) -> str:
    """Create a temp DB with a workouts table.

    *workouts*: list of (days_before_ref, minutes, workout_type).
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sources ("
        "source_id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, "
        "name TEXT, kind TEXT, bundle_id TEXT, device_model TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    con.execute(
        "INSERT INTO sources VALUES "
        "(1,'t.watch','Watch','watch','com.t','Watch',"
        "'2031-01-01T00:00:00Z','2031-01-01T00:00:00Z')"
    )
    con.execute(
        "CREATE TABLE workouts ("
        "workout_id integer primary key, source_id integer not null "
        "references sources(source_id), client_record_id text not null, "
        "workout_type text not null, start_time text not null, "
        "end_time text not null, duration_seconds integer not null, "
        "energy_kcal real, distance_meters real, "
        "unique (source_id, client_record_id))"
    )
    for i, (days, minutes, wtype) in enumerate(workouts):
        d = _REF - timedelta(days=days)
        end = f"{d.isoformat()}T18:00:00Z"  # 13:00 Chicago
        if minutes < 60:
            start = f"{d.isoformat()}T17:{60 - minutes:02d}:00Z"
        else:
            start = f"{d.isoformat()}T16:00:00Z"
        con.execute(
            "INSERT INTO workouts "
            "(source_id, client_record_id, workout_type, start_time, end_time, "
            "duration_seconds) VALUES (1,?,?,?,?,?)",
            (f"w{i}", wtype, start, end, minutes * 60),
        )
    con.commit()
    con.close()
    return path


# ---------------------------------------------------------------------------
# 1. Missing table → no crash, empty list
# ---------------------------------------------------------------------------

def test_missing_table_returns_empty():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    results = workout_rules.workout_findings(path, _REF)
    assert results == []


# ---------------------------------------------------------------------------
# 2. No workouts at all → empty list
# ---------------------------------------------------------------------------

def test_no_workouts_returns_empty():
    path = _make_db([])
    results = workout_rules.workout_findings(path, _REF)
    assert results == []


# ---------------------------------------------------------------------------
# 3. workout_consistency — two workouts this week (positive finding)
# ---------------------------------------------------------------------------

def test_workout_consistency_two_workouts_this_week():
    # Both workouts within ref-6..ref
    path = _make_db([(2, 20, "walking"), (5, 15, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    f: Finding = results[0]
    assert f.id == "workout_consistency"
    assert f.level == 0
    assert "2" in f.evidence  # count of workouts
    assert "min" in f.evidence.lower()


# ---------------------------------------------------------------------------
# 4. workout_load_spike — this week >> baseline average
# ---------------------------------------------------------------------------

def test_workout_load_spike_detected():
    # This week: 90+80 = 170 min. Baseline: 4×20 = 80, avg=20. 170 > 1.5×20.
    workouts = [(1, 90, "running"), (3, 80, "running")]
    workouts += [(7 + 7 * k + 2, 20, "walking") for k in range(4)]
    path = _make_db(workouts)
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    f: Finding = results[0]
    assert f.id == "workout_load_spike"
    assert f.level == 1
    assert "week" in f.evidence.lower()


# ---------------------------------------------------------------------------
# 5. no_recent_workouts — gap > 14 days
# ---------------------------------------------------------------------------

def test_no_recent_workouts_gap():
    path = _make_db([(20, 30, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    f: Finding = results[0]
    assert f.id == "no_recent_workouts"
    assert f.level == 1
    assert "14" in f.evidence
    assert "clinician" not in f.advice.lower()


# ---------------------------------------------------------------------------
# 6. No finding when last workout is 10 days ago (within 14-day window)
# ---------------------------------------------------------------------------

def test_no_gap_within_fourteen_days():
    path = _make_db([(10, 30, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert results == []


# ---------------------------------------------------------------------------
# 7. first-ever workout this week — no baseline, positive consistency
# ---------------------------------------------------------------------------

def test_first_ever_recent_workout():
    path = _make_db([(3, 25, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    assert results[0].id == "workout_consistency"
    assert results[0].level == 0


# ---------------------------------------------------------------------------
# 8. Spike boundary: exactly 1.5× does NOT trigger spike
# ---------------------------------------------------------------------------

def test_spike_boundary_exactly_1_5x_no_spike():
    # This week: 30 min. Baseline 4 weeks: 20+20+20+20 = 80, avg=20.
    # 30 == 1.5 × 20 → NOT > 1.5, so no spike.
    workouts = [(1, 30, "running")]
    workouts += [(7 + 7 * k + 2, 20, "walking") for k in range(4)]
    path = _make_db(workouts)
    results = workout_rules.workout_findings(path, _REF)
    # Should fall through to consistency (1 workout this week)
    assert len(results) == 1
    assert results[0].id == "workout_consistency"


# ---------------------------------------------------------------------------
# 9. Spike triggers over boundary: 1.5× + 1 min
# ---------------------------------------------------------------------------

def test_spike_over_boundary():
    # This week: 31 min. Baseline avg = 20. 31 > 30 → spike.
    workouts = [(1, 31, "running")]
    workouts += [(7 + 7 * k + 2, 20, "walking") for k in range(4)]
    path = _make_db(workouts)
    results = workout_rules.workout_findings(path, _REF)
    assert results[0].id == "workout_load_spike"


# ---------------------------------------------------------------------------
# 10. Priority: spike takes precedence over consistency
# ---------------------------------------------------------------------------

def test_priority_spike_over_consistency():
    # Plenty of workouts this week + spike → only spike, not consistency
    workouts = [(1, 90, "running"), (2, 80, "cycling")]
    workouts += [(7 + 7 * k + 2, 20, "walking") for k in range(4)]
    path = _make_db(workouts)
    results = workout_rules.workout_findings(path, _REF)
    ids = [f.id for f in results]
    assert ids == ["workout_load_spike"]


# ---------------------------------------------------------------------------
# 11. Priority: consistency over no_recent_workouts
# ---------------------------------------------------------------------------

def test_priority_consistency_over_gap():
    # Workout this week AND >14-day workout exists → only consistency
    workouts = [(2, 20, "walking"), (20, 30, "walking")]
    path = _make_db(workouts)
    results = workout_rules.workout_findings(path, _REF)
    ids = [f.id for f in results]
    assert ids == ["workout_consistency"]


# ---------------------------------------------------------------------------
# 12. Singular/plural evidence text
# ---------------------------------------------------------------------------

def test_singular_plural_evidence():
    path = _make_db([(2, 20, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    assert "1 workout" in results[0].evidence  # singular


def test_plural_evidence():
    path = _make_db([(2, 20, "walking"), (3, 15, "cycling")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    assert "2 workouts" in results[0].evidence  # plural


def test_two_sessions_on_the_same_day_count_as_two_workouts():
    # Review fix (2026-09-27): the count used to bucket by distinct Chicago DAY,
    # so two sessions on one day showed "1 workout" here while workouts.py's own
    # weekly_line (shown next to this on the card) said "2 in 7d" for the same
    # data. Match weekly_line's definition: a session count, not a day count.
    from health_insights import workouts as workouts_mod
    path = _make_db([(2, 20, "walking"), (2, 15, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    assert results[0].id == "workout_consistency"
    assert "2 workouts" in results[0].evidence
    line = workouts_mod.weekly_line(path, _REF.isoformat())
    assert line is not None and line.startswith("Workouts: 2 in 7d")


# ---------------------------------------------------------------------------
# 13. 14-day boundary: exactly 14 days → no gap
# ---------------------------------------------------------------------------

def test_gap_boundary_exactly_14_days():
    path = _make_db([(14, 30, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    # Exactly 14 days — not *exceeding* 14
    assert results == []


# ---------------------------------------------------------------------------
# 14. 15 days → gap triggers
# ---------------------------------------------------------------------------

def test_gap_boundary_15_days():
    path = _make_db([(15, 30, "walking")])
    results = workout_rules.workout_findings(path, _REF)
    assert len(results) == 1
    assert results[0].id == "no_recent_workouts"


# ---------------------------------------------------------------------------
# 15. ref_date as string works too
# ---------------------------------------------------------------------------

def test_ref_date_string():
    path = _make_db([(2, 20, "walking")])
    results = workout_rules.workout_findings(path, "2031-08-10")
    assert len(results) == 1
    assert results[0].id == "workout_consistency"


# ---------------------------------------------------------------------------
# Review fix (2026-09-27): the baseline weeks must be 4 distinct, non-overlapping
# 7-day windows that never overlap "this week" (the bot's original window math
# reused the whole 28-day range for k=0 and, for k>=1, slid into "this week").
# ---------------------------------------------------------------------------

def test_baseline_weeks_do_not_overlap_this_week():
    workouts = [(1, 90, "running"), (3, 80, "running")] + [(7 + 7 * k + 2, 20, "walking") for k in range(4)]
    path = _make_db(workouts)
    results = workout_rules.workout_findings(path, _REF)
    assert [f.id for f in results] == ["workout_load_spike"], results
