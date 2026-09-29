"""
Blood pressure monitor (descriptive bands only; not medical advice).

Pairs systolic/diastolic samples, groups them by the Chicago calendar day,
classifies a reading and a 7-day average against US home-BP bands, and emits
stable flags. Health data stays local; this module never prints values across
process boundaries.

Category table (highest band wins; check systolic, then diastolic):
  Normal   sys < 120 AND dia < 80
  Elevated sys 120-129 AND dia < 80
  Stage 1  sys 130-139 OR  dia 80-89
  Stage 2  sys 140-180 OR  dia 90-120
  Severe   sys > 180 OR    dia > 120

A reading is a pair sharing the same ``start_time``; an unpaired value is
ignored. All day boundaries use America/Chicago, never UTC.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as Date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from . import bridge_db

CHICAGO = bridge_db.CHICAGO

SYS_CODE = "blood_pressure_systolic"
DIA_CODE = "blood_pressure_diastolic"

# Lowest -> highest. The higher of the two bands wins.
BAND_ORDER = ("Normal", "Elevated", "Stage 1", "Stage 2", "Severe")
_BAND_RANK = {b: i for i, b in enumerate(BAND_ORDER)}


def _round_half_up(value: float) -> int:
    """Round to the nearest whole mmHg, halves rounding away from zero."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _systolic_band(sys_val: float) -> str:
    if sys_val > 180:
        return "Severe"
    if sys_val >= 140:
        return "Stage 2"
    if sys_val >= 130:
        return "Stage 1"
    if sys_val >= 120:
        return "Elevated"
    return "Normal"


def _diastolic_band(dia_val: float) -> str:
    if dia_val > 120:
        return "Severe"
    if dia_val >= 90:
        return "Stage 2"
    if dia_val >= 80:
        return "Stage 1"
    return "Normal"


def category(systolic: float, diastolic: float) -> str:
    """Classify one reading; the higher of the two bands wins."""
    sys_band = _systolic_band(systolic)
    dia_band = _diastolic_band(diastolic)
    return sys_band if _BAND_RANK[sys_band] >= _BAND_RANK[dia_band] else dia_band


def _readings(db_path: str) -> list[tuple[str, str, float, float]]:
    """Return paired readings as (day, time_key, systolic, diastolic).

    ``day`` is the Chicago calendar date; ``time_key`` is the local time of day.
    Only samples sharing a ``start_time`` on both codes form a reading; an
    unpaired value is dropped.
    """
    conn = bridge_db.open_readonly(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT type_code, start_time, value FROM samples "
            f"WHERE type_code IN ('{SYS_CODE}', '{DIA_CODE}') AND value IS NOT NULL"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    groups: dict[str, dict[str, float]] = {}
    for type_code, start, value in rows:
        norm = bridge_db._normalize_ts(start) or start
        groups.setdefault(norm, {})[type_code] = value

    readings: list[tuple[str, str, float, float]] = []
    for norm_ts, pair in groups.items():
        if SYS_CODE not in pair or DIA_CODE not in pair:
            continue
        try:
            local = datetime.fromisoformat(norm_ts).astimezone(CHICAGO)
        except ValueError:
            continue
        readings.append(
            (local.strftime("%Y-%m-%d"), local.strftime("%H:%M"), pair[SYS_CODE], pair[DIA_CODE])
        )
    return readings


def _latest(readings: list) -> tuple[str, str, float, float] | None:
    """The most recent reading by (day, time)."""
    return max(readings, key=lambda r: (r[0], r[1])) if readings else None


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _seven_day_avg(readings: list, ref_date: Date) -> tuple[str, int, int] | None:
    """Average paired readings over the 7 days ending on ``ref_date``.

    Returns (category, reading_count, day_count) or None when fewer than 3
    readings fall in the window.
    """
    start = (ref_date - timedelta(days=6)).isoformat()
    window = [r for r in readings if start <= r[0] <= ref_date.isoformat()]
    if len(window) < 3:
        return None
    sys_avg = _avg([r[2] for r in window])
    dia_avg = _avg([r[3] for r in window])
    return category(_round_half_up(sys_avg), _round_half_up(dia_avg)), len(window), len({r[0] for r in window})


def _flags(readings: list, ref_date: Date) -> list[str]:
    """Return the ordered BP flags for the day ending on ``ref_date``.

    SEVERE_READING freshness (2026-09-22 review): the flag reflects the latest reading only within the same
    4-day recency window used by NO_READING_4D. A reading outside that window is exactly what NO_READING_4D
    already exists to describe ("no reading"), so an old severe reading does not keep flagging as current
    forever, and a reading dated after ref_date (bad import, clock skew) can never affect an earlier report.
    """
    flags: list[str] = []

    # Shared 4-day recency window for SEVERE_READING and NO_READING_4D: readings strictly at or before
    # ref_date only, so a future-dated reading never affects a report for an earlier reference date.
    recent_start = (ref_date - timedelta(days=3)).isoformat()
    recent = [r for r in readings if recent_start <= r[0] <= ref_date.isoformat()]

    latest = _latest(recent)
    if latest and category(latest[2], latest[3]) == "Severe":
        flags.append("SEVERE_READING")

    seven = _seven_day_avg(readings, ref_date)
    if seven:
        cat, _, _ = seven
        if cat == "Stage 2":
            flags.append("STAGE2_AVG")
        elif cat == "Stage 1":
            flags.append("STAGE1_AVG")

    # RISING: 7d systolic average at least 10 mmHg above the 14-28 day window.
    seven_start = (ref_date - timedelta(days=6)).isoformat()
    seven_sys = [r[2] for r in readings if seven_start <= r[0] <= ref_date.isoformat()]
    prior_start = (ref_date - timedelta(days=28)).isoformat()
    prior_end = (ref_date - timedelta(days=14)).isoformat()
    prior_sys = [r[2] for r in readings if prior_start <= r[0] <= prior_end]
    if len(seven_sys) >= 7 and len(prior_sys) >= 7:
        if _avg(seven_sys) - _avg(prior_sys) >= 10:
            flags.append("RISING")

    if not recent:
        flags.append("NO_READING_4D")

    return flags


def _fmt_date(d: str) -> str:
    return f"{Date.fromisoformat(d):%b} {int(d[8:10])}"


def report(db_path: str, ref_date: str) -> list[str]:
    """Build the BP CLI lines for the Chicago day ``ref_date`` (YYYY-MM-DD)."""
    ref = Date.fromisoformat(ref_date)
    readings = _readings(db_path)
    # A reading dated after ref_date (bad import, clock skew) must never appear as "latest" in a report for an
    # earlier date (2026-09-22 review, same root cause as the SEVERE_READING freshness fix in _flags).
    latest = _latest([r for r in readings if r[0] <= ref_date])
    lines: list[str] = []

    if latest:
        # Outside the 4-day window the flags line says NO_READING_4D, so a Severe category here would read as a
        # contradiction: state the reading's age instead of leaving the reader to work it out (review 2026-09-22).
        age = (ref - Date.fromisoformat(latest[0])).days
        stale = f", {age} days ago" if age > 3 else ""
        lines.append(
            f"BP latest: {_round_half_up(latest[2])}/{_round_half_up(latest[3])} mmHg "
            f"on {_fmt_date(latest[0])} ({category(latest[2], latest[3])}{stale})"
        )

    seven = _seven_day_avg(readings, ref)
    if seven:
        cat, count, days = seven
        lines.append(
            f"BP 7d avg: {_round_half_up(_avg([r[2] for r in _window(readings, ref)]))}/"
            f"{_round_half_up(_avg([r[3] for r in _window(readings, ref)]))} mmHg "
            f"from {count} readings on {days} days ({cat})"
        )
    elif readings:
        count = len([r for r in readings if (ref - timedelta(days=6)).isoformat() <= r[0] <= ref.isoformat()])
        lines.append(f"BP 7d avg: insufficient ({count} readings; need 3)")

    flags = _flags(readings, ref)
    lines.append(f"BP flags: {' '.join(flags) if flags else 'none'}")
    return lines


def _window(readings: list, ref: Date) -> list:
    start = (ref - timedelta(days=6)).isoformat()
    return [r for r in readings if start <= r[0] <= ref.isoformat()]
