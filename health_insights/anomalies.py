"""
Anomaly detector for health metrics.

Reads a bridge/history SQLite DB and flags days whose value for a configured
metric deviates from its own recent history. The math is stdlib only (median,
MAD, robust z, least-squares slope); Hermes narrates. Informational only — not
medical advice. No values are written anywhere.

Contract:
    detect(db_path, config, date) -> dict

    {
      "<type_code>": {
        "value": <float|null>,
        "status": "ok|flagged|insufficient",
        "reason": "<short human summary, counts/bounds only>"
      },
      ...
    }

A metric is "insufficient" when it has fewer than MIN_HISTORY_DAYS distinct
local days of history; insufficient metrics are never flagged.

The baseline is the prior baseline_window_days CALENDAR days before the target
date (Chicago day keys, target excluded), not the last N days that happened to
contain samples. When fewer than MIN_BASELINE_DAYS of those days have a value,
the robust-z and trend checks are skipped (a median/MAD or slope over a handful
of points flags on noise) and the reason says so; absolute bounds and the
weight change check need no baseline and still run.

Daily value computation:
  * samples-based metrics: mean of all sample values on that local day.
  * sleep_hours: sum of sleep_sessions durations that start on that local day.

Flagging (a metric is flagged when ANY of these hold):
  * |robust_z(value)| >= robust_sigma, using the median + MAD of the daily values
    in the prior baseline_window_days calendar days (target excluded), only when
    at least MIN_BASELINE_DAYS of those days have a value.
  * value is outside [min_abs, max_abs] (when both bounds are set).
  * the baseline-window trend slope exceeds trend.slope_per_day_alert in
    magnitude (same MIN_BASELINE_DAYS requirement).
  * weight additionally: the change over window_days exceeds max_change_over_days.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from . import stats as st
from .bridge_db import CHICAGO, _normalize_ts
from health_insights import settings

MIN_HISTORY_DAYS = 14  # below this, a metric is "insufficient" and never flagged
MIN_BASELINE_DAYS = 14  # in-window days needed for robust_z / trend (same rule as morning_digest)


def _open(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _daily_means(conn: sqlite3.Connection, type_code: str, chi: ZoneInfo) -> dict[str, list[float]]:
    """Group sample values by local calendar date for one type_code."""
    cur = conn.cursor()
    cur.execute(
        "SELECT start_time, value FROM samples WHERE type_code = ? AND value IS NOT NULL",
        (type_code,),
    )
    by_date: dict[str, list[float]] = defaultdict(list)
    for start_time, value in cur.fetchall():
        norm = _normalize_ts(start_time)
        if not norm:
            continue
        d = datetime.fromisoformat(norm).astimezone(chi).strftime("%Y-%m-%d")
        by_date[d].append(float(value))
    return by_date


def _daily_sleep_hours(conn: sqlite3.Connection, chi: ZoneInfo) -> dict[str, float]:
    """Sum sleep_sessions durations by WAKE-UP date: the local calendar day each session ends on.

    Returns an empty dict when the DB has no sleep_sessions table (e.g. the
    merged history DB), so the detector labels the metric insufficient.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sleep_sessions'"
    )
    if cur.fetchone() is None:
        return {}
    cur.execute(
        "SELECT start_time, end_time FROM sleep_sessions "
        "WHERE start_time IS NOT NULL AND start_time != '' AND end_time IS NOT NULL"
    )
    by_date: dict[str, float] = {}
    for start_time, end_time in cur.fetchall():
        s = _normalize_ts(start_time)
        e = _normalize_ts(end_time)
        if not s or not e:
            continue
        start_dt = datetime.fromisoformat(s)
        end_dt = datetime.fromisoformat(e)
        dur = (end_dt - start_dt).total_seconds() / 3600.0
        if dur <= 0:
            continue
        # Group by the local date the session ENDS: a night that starts after midnight and the next night that
        # starts in the evening of the same date are different nights and must not be summed together.
        d = end_dt.astimezone(chi).strftime("%Y-%m-%d")
        by_date[d] = by_date.get(d, 0.0) + dur
    return by_date


def _daily_values(db_path: str, config: dict, type_code: str, chi: ZoneInfo) -> dict[str, float]:
    """Return {local_date: daily_value} for one configured metric."""
    metric = config["metrics"][type_code]
    agg = metric.get("aggregate", "mean")
    if agg == "sleep_hours":
        return _daily_sleep_hours(_open(db_path), chi)
    by_date = _daily_means(_open(db_path), type_code, chi)
    return {d: st.mean(v) for d, v in by_date.items()}


def _previous_window(dates: list[str], target: str, window_days: int) -> list[str]:
    """Dates strictly before `target` that fall within the prior `window_days` CALENDAR days (2026-09-22
    review fix). Sparse data must not reach back arbitrarily far to pad out a fixed count of sample-dates -
    a date more than `window_days` calendar days before `target` is excluded even if it is the most recent
    sample there is. Shared by the detector and morning_digest.py's baseline selection, so both stay
    consistent by construction.
    """
    earlier = [d for d in dates if d < target]
    if window_days and window_days > 0:
        cutoff = (datetime.fromisoformat(target) - timedelta(days=window_days)).strftime("%Y-%m-%d")
        earlier = [d for d in earlier if d >= cutoff]
    return earlier


def detect(db_path: str, config: dict, date) -> dict:
    """Flag anomalous days for each configured metric. Counts/bounds only.

    `date` may be a "YYYY-MM-DD" string or a datetime; both are normalised to a
    local calendar day in the config timezone before any comparison.
    """
    chi = ZoneInfo(config.get("timezone", settings.timezone_name()))
    if isinstance(date, datetime):
        date = date.astimezone(chi).strftime("%Y-%m-%d")
    elif not isinstance(date, str):
        date = str(date)
    result: dict[str, dict] = {}

    # Read all daily values once per metric.
    daily: dict[str, dict[str, float]] = {}
    for tc in config["metrics"]:
        daily[tc] = _daily_values(db_path, config, tc, chi)

    for tc, metric in config["metrics"].items():
        values_by_date = daily[tc]
        dates = sorted(values_by_date)
        # History means days strictly before `date`: never look ahead at later data.
        history = [d for d in dates if d < date]

        # Insufficient history: fewer than MIN_HISTORY_DAYS distinct local days before `date`.
        if len(history) < MIN_HISTORY_DAYS:
            result[tc] = {
                "value": None,
                "status": "insufficient",
                "reason": f"{len(history)} day(s) of history",
            }
            continue

        window_days = metric.get("baseline_window_days", 30)
        baseline_dates = _previous_window(dates, date, window_days)
        baseline_vals = [values_by_date[d] for d in baseline_dates if d in values_by_date]

        value = values_by_date.get(date)
        if value is None:
            result[tc] = {
                "value": None,
                "status": "insufficient",
                "reason": f"{len(history)} day(s) of history",
            }
            continue

        flags: list[str] = []

        # Absolute bounds first (hard physical limits).
        bounds = metric.get("anomaly", {})
        if bounds.get("min_abs") is not None and value < bounds["min_abs"]:
            flags.append(f"below min_abs={bounds['min_abs']}")
        if bounds.get("max_abs") is not None and value > bounds["max_abs"]:
            flags.append(f"above max_abs={bounds['max_abs']}")

        # Robust z-score and trend need a real baseline. The calendar window can hold far fewer
        # values than the history gate guarantees (sparse metrics such as weight), and a median/MAD
        # or slope over two or three points flags on noise, so both checks are skipped below
        # MIN_BASELINE_DAYS in-window days and the thin baseline is named in the reason.
        baseline_ok = len(baseline_vals) >= MIN_BASELINE_DAYS
        notes: list[str] = []
        if not baseline_ok:
            notes.append(f"baseline {len(baseline_vals)}/{MIN_BASELINE_DAYS} days in window (z/trend skipped)")

        if baseline_ok:
            z = st.robust_z(value, baseline_vals)
            if abs(z) >= metric["anomaly"]["robust_sigma"]:
                flags.append(f"robust_z={z:+.1f}")

        # Trend slope over the baseline window.
        trend = metric.get("trend") or {}
        if baseline_ok and trend.get("slope_per_day_alert") is not None and len(baseline_dates) >= 2:
            xs = [float(datetime.fromisoformat(d[:10]).replace(tzinfo=timezone.utc).timestamp()) for d in baseline_dates]
            ys = [values_by_date[d] for d in baseline_dates]
            slope = st.slope(xs, ys) * 86400  # per-day
            if abs(slope) >= trend["slope_per_day_alert"]:
                flags.append(f"slope={slope:+.3f}/d")

        # Weight change over window_days.
        if tc == "weight":
            change_cfg = bounds.get("max_change_over_days")
            window = bounds.get("window_days", 3)
            if change_cfg is not None:
                # Compare today's value to the value `window` days earlier by calendar subtraction (a
                # datetime.replace(day=...) clamp was wrong across a month/year boundary - review 2026-09-22).
                target_dt = datetime.fromisoformat(date)
                other_dt = target_dt - timedelta(days=window)
                other_date = other_dt.strftime("%Y-%m-%d")
                if other_date in values_by_date:
                    delta = abs(value - values_by_date[other_date])
                    if delta >= change_cfg:
                        flags.append(f"{window}d change={delta:+.1f}")

        if flags:
            result[tc] = {"value": value, "status": "flagged", "reason": "; ".join(flags + notes)}
        else:
            result[tc] = {"value": value, "status": "ok", "reason": "; ".join(notes)}

    return result
