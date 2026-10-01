"""Workout aggregation: weekly summary line and daily digest line.

Reads the merged history file (read-only) and exposes:

    weekly_line(db_path, ref_date) -> str | None
    digest_line(db_path, date) -> str | None

Workout times end in ``Z``; heart-rate sample times use ``+00:00``.
Both are parsed to timezone-aware datetimes and compared as instants.

A workout belongs to the America/Chicago calendar date of its END time.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from health_insights import settings
from health_insights.sqlite_ro import connect_readonly

CHI = settings.timezone()


def _open(db_path: str) -> sqlite3.Connection:
    """Open a merged-history DB for read-only access."""
    return connect_readonly(db_path)


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO timestamp (Z or +00:00) into a timezone-aware UTC datetime."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _workout_chicago_date(end_time: str) -> str:
    """Return the Chicago date string for a workout's end time."""
    utc_dt = _parse_ts(end_time)
    chi_dt = utc_dt.astimezone(CHI)
    return chi_dt.strftime("%Y-%m-%d")


def _has_workouts_table(db_path: str) -> bool:
    """Check whether the workouts table exists."""
    conn = _open(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='workouts'"
    )
    result = cur.fetchone()
    conn.close()
    return result is not None


def weekly_line(db_path: str, ref_date: str) -> str | None:
    """Return the weekly workouts summary line for ref_date (Chicago).

    Window: ref_date-6 .. ref_date (Chicago dates).
    Returns None when the table is missing or empty.
    """
    if not _has_workouts_table(db_path):
        return None

    ref = datetime.fromisoformat(ref_date).date()
    window_start = (ref - timedelta(days=6)).isoformat()
    window_end = ref.isoformat()

    conn = _open(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM workouts")
    total = cur.fetchone()[0]
    conn.close()

    if total == 0:
        return None

    # Fetch all workouts with their Chicago end dates
    conn = _open(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT workout_type, duration_seconds, energy_kcal, distance_meters, end_time "
        "FROM workouts"
    )
    rows = cur.fetchall()
    conn.close()

    # Filter to window
    window_rows = []
    for wtype, dur, kcal, meters, end_time in rows:
        ch_date = _workout_chicago_date(end_time)
        if window_start <= ch_date <= window_end:
            window_rows.append((wtype, dur, kcal, meters))

    if not window_rows:
        return "Workouts: none in the last 7 days"

    # Aggregate by type
    from collections import Counter, defaultdict
    type_counts: Counter = Counter()
    type_durations: dict[str, int] = defaultdict(int)
    total_seconds = 0
    total_kcal = 0.0
    kcal_count = 0
    total_meters = 0.0
    meters_count = 0

    for wtype, dur, kcal, meters in window_rows:
        type_counts[wtype] += 1
        type_durations[wtype] += dur
        total_seconds += dur
        if kcal is not None:
            total_kcal += kcal
            kcal_count += 1
        if meters is not None:
            total_meters += meters
            meters_count += 1

    total_count = sum(type_counts.values())
    minutes = round(total_seconds / 60)

    # Build type list: sorted by count desc, then name asc
    type_parts = []
    for wtype, count in sorted(type_counts.items(), key=lambda x: (-x[1], x[0])):
        name = wtype.replace("_", " ")
        type_parts.append(f"{count} {name}")

    type_str = ", ".join(type_parts)

    # Build the line
    parts = [f"Workouts: {total_count} in 7d, {minutes} min ({type_str})"]

    if kcal_count > 0:
        parts.append(f"{int(round(total_kcal))} kcal")
    if meters_count > 0:
        parts.append(f"{total_meters / 1000:.1f} km")

    return ", ".join(parts)


def digest_line(db_path: str, date: str) -> str | None:
    """Return the daily digest line(s) for *date* (Chicago).

    One workout: "Workout: 23 min walking" + HR text if ≥3 samples in [start, end].
    Several workouts: "Workouts (2): 40 min running; 30 min strength training" (start order, no HR).
    None when there are no workouts for that date.
    """
    if not _has_workouts_table(db_path):
        return None

    conn = _open(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT workout_type, start_time, end_time, duration_seconds "
        "FROM workouts"
    )
    rows = cur.fetchall()
    conn.close()

    # Filter to the requested Chicago date
    day_rows = []
    for wtype, start, end, dur in rows:
        ch_date = _workout_chicago_date(end)
        if ch_date == date:
            day_rows.append((wtype, start, end, dur))

    if not day_rows:
        return None

    # Sort by start time (UTC instant)
    day_rows.sort(key=lambda r: _parse_ts(r[1]))

    conn = _open(db_path)
    cur = conn.cursor()

    if len(day_rows) == 1:
        # Single workout: check for HR samples
        wtype, start, end, dur = day_rows[0]
        minutes = round(dur / 60)
        hr_result = _hr_stats(conn, start, end)
        if hr_result:
            avg, mx = hr_result
            return f"Workout: {minutes} min {wtype.replace('_', ' ')}, HR avg {avg} max {mx} bpm"
        return f"Workout: {minutes} min {wtype.replace('_', ' ')}"
    else:
        # Multiple workouts: semicolon-separated, no HR
        parts = []
        for wtype, start, end, dur in day_rows:
            minutes = round(dur / 60)
            parts.append(f"{minutes} min {wtype.replace('_', ' ')}")
        return f"Workouts ({len(day_rows)}): {'; '.join(parts)}"


def _hr_stats(conn: sqlite3.Connection, start: str, end: str) -> tuple[int, int] | None:
    """Return (avg, max) heart rate if ≥3 samples in [start, end], else None."""
    cur = conn.cursor()
    # Parse start and end as UTC instants
    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)

    # HR samples use +00:00 format; compare instants
    cur.execute(
        "SELECT start_time, value FROM samples "
        "WHERE type_code = 'heart_rate' AND start_time IS NOT NULL"
    )
    hr_rows = cur.fetchall()

    count = 0
    total = 0
    mx = 0
    for sample_ts, val in hr_rows:
        if val is None:
            continue
        sample_dt = _parse_ts(sample_ts)
        if start_dt <= sample_dt <= end_dt:
            count += 1
            total += val
            if val > mx:
                mx = val

    if count >= 3:
        avg = round(total / count)
        return (int(avg), int(mx))
    return None
