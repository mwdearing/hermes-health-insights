"""
Weekly health trends report (no agent, no model).

Compares each metric's last 7 days (date-6 .. date) with the 28 days before
them (date-34 .. date-7), plus blood pressure and a "Notable" summary line.
Health data stays local; the report prints aggregates only, never values across
process boundaries. Informational only -- not medical advice.

Contract:
    report(db_path, config, ref_date) -> list[str]

The metric order in the report follows the config's metric keys, rendered with
the labels in METRIC_LABELS. A metric is reported as its 7-day mean versus the
28-day baseline median; the direction (up/down/flat) uses the 28-day MAD-based
robust sigma so a small change over a tight baseline is not called a trend.
"""
from __future__ import annotations

import sqlite3
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from . import stats as st
from .bridge_db import _normalize_ts
from .bp import _readings as bp_readings
from .units import to_display_weight, weight_unit

# (config type_code, label, unit). Weight is stored in kg and shown in lb; SpO2 (oxygen_saturation)
# is stored as a fraction and rendered as a percent.
METRIC_LABELS: tuple[tuple[str, str, str], ...] = (
    ("sleep_analysis", "Sleep", "h"),
    ("resting_heart_rate", "Resting HR", "bpm"),
    ("heart_rate_variability_sdnn", "HRV", "ms"),
    ("weight", "Weight", weight_unit()),
    ("oxygen_saturation", "SpO2", "%"),
    ("heart_rate", "Heart rate", "bpm"),
)

from health_insights import settings
RECENT_DAYS = 7      # date-6 .. date
BASELINE_DAYS = 28   # date-34 .. date-7
MIN_RECENT_DAYS = 5  # fewer recent days of data -> "insufficient"
MIN_BASELINE_DAYS = 5  # fewer baseline days -> no direction (still prints the mean)
DIRECTION_SIGMA = 1.0  # a change must reach this many robust sigmas to be up/down


def _open(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _daily_values(db_path: str, config: dict, type_code: str, chi: ZoneInfo) -> dict[str, float]:
    """{chicago_date: daily_value} for one metric: the detector's own aggregation (sleep grouped by wake-up
    date), so the weekly report and the morning digest can never disagree on a day's value."""
    from . import anomalies
    metric = config["metrics"][type_code]
    if metric.get("aggregate", "mean") == "sleep_hours":
        return anomalies._daily_values(db_path, config, type_code, chi)
    bounds = metric.get("anomaly") or {}
    lo, hi = bounds.get("min_abs"), bounds.get("max_abs")
    out: dict[str, float] = {}
    for d, vals in anomalies._daily_means(_open(db_path), type_code, chi).items():
        kept = [v for v in vals
                if (lo is None or v >= lo) and (hi is None or v <= hi)]
        if kept:
            out[d] = sum(kept) / len(kept)
    return out


def _fmt(n: float | None, ndigits: int = 1) -> str:
    """Format a number with at most `ndigits` decimals, dropping trailing zeros."""
    if n is None:
        return "n/a"
    s = f"{n:.{ndigits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _direction(recent_mean: float, baseline_vals: list[float]) -> str:
    """up/down when the change reaches `DIRECTION_SIGMA` robust sigmas, else flat.

    The baseline median is the reference; the robust sigma is 1.4826 * MAD of the
    28-day baseline values. A degenerate (all-identical) baseline has sigma 0, so any
    non-zero change exceeds it and is called up/down; a zero change is always flat.
    """
    baseline_median = st.median(baseline_vals) if baseline_vals else 0.0
    change = recent_mean - baseline_median
    if change == 0:
        return "flat"
    std = st.robust_std(baseline_vals) if baseline_vals else 0.0
    if abs(change) >= DIRECTION_SIGMA * std:
        return "up" if change > 0 else "down"
    return "flat"


def _metric_line(tc: str, label: str, unit: str, ref_date: str, series: dict[str, float]) -> str:
    """One weekly-report line for a single metric, or an insufficient-history line."""
    dates = sorted(series)
    if not dates:
        return f"{label}: no data"

    # Windows anchored on the reference date, not the last day with data.
    recent_start = (Date.fromisoformat(ref_date) - timedelta(days=RECENT_DAYS - 1)).isoformat()
    baseline_start = (Date.fromisoformat(ref_date) - timedelta(days=RECENT_DAYS + BASELINE_DAYS - 1)).isoformat()
    recent = [d for d in dates if d >= recent_start]
    baseline = [d for d in dates if baseline_start <= d < recent_start]

    recent_vals = [series[d] for d in recent]
    baseline_vals = [series[d] for d in baseline]
    recent_mean = st.mean(recent_vals)
    baseline_median = st.median(baseline_vals) if baseline_vals else 0.0

    recent_days = len(recent)
    # Fewer than MIN_RECENT_DAYS of the recent window have data -> insufficient, no direction.
    if recent_days < MIN_RECENT_DAYS:
        return f"{label}: not enough data ({recent_days} of {RECENT_DAYS} days)"

    direction = _direction(recent_mean, baseline_vals) if len(baseline_vals) >= MIN_BASELINE_DAYS else "flat"
    sign = "+" if (recent_mean - baseline_median) >= 0 else "-"
    change = abs(recent_mean - baseline_median)

    if tc == "weight":
        wu = weight_unit()
        return (f"{label}: 7d {_fmt(to_display_weight(recent_mean))} {wu} vs 28d {_fmt(to_display_weight(baseline_median))} "
                f"{wu} ({sign}{to_display_weight(change):.1f}, {direction})")

    if tc == "oxygen_saturation":
        # Stored as a fraction; render as a percent. Recompute sign/change after scaling.
        recent_mean = recent_mean * 100
        baseline_median = baseline_median * 100
        sign = "+" if (recent_mean - baseline_median) >= 0 else "-"
        change = abs(recent_mean - baseline_median)

    return (f"{label}: 7d {_fmt(recent_mean)} {unit} vs 28d {_fmt(baseline_median)} "
            f"{unit} ({sign}{change:.1f}, {direction}), {recent_days} of {RECENT_DAYS} days")


def _bp_line(db_path: str, ref_date: str) -> str | None:
    """Compare BP systolic over the last 7 days with the 28 days before them."""
    readings = bp_readings(db_path)
    if not readings:
        return None
    days = sorted({r[0] for r in readings})
    last = Date.fromisoformat(days[-1])
    recent_start = (last - timedelta(days=RECENT_DAYS - 1)).isoformat()
    baseline_start = (last - timedelta(days=BASELINE_DAYS)).isoformat()
    recent = [r[2] for r in readings if r[0] >= recent_start]
    baseline = [r[2] for r in readings if baseline_start <= r[0] < recent_start]
    if not recent:
        return None
    sys_7 = st.mean(recent)
    sys_28 = st.median(baseline) if baseline else 0.0
    dia_7 = st.mean([r[3] for r in readings if r[0] >= recent_start])
    dia_28 = st.median([r[3] for r in readings if baseline_start <= r[0] < recent_start]) if baseline else 0.0
    direction = _direction(sys_7, baseline)
    sign = "+" if (sys_7 - sys_28) > 0 else ""
    change = abs(sys_7 - sys_28)
    return (f"BP: 7d {_fmt(sys_7, 0)}/{_fmt(dia_7, 0)} mmHg vs 28d "
            f"{_fmt(sys_28, 0)}/{_fmt(dia_28, 0)} mmHg ({sign}{change:.0f}, {direction})")


def _notable(lines: list[str]) -> str:
    """Name the up/down metrics across the metric lines and the BP line."""
    notable: list[str] = []
    for line in lines:
        if " up)" in line or line.endswith(" up)") or ", up)" in line:
            label = line.split(":")[0].split(" up")[0].strip()
            if label and label != "Notable":
                notable.append(f"{label} up")
        elif ", down)" in line:
            label = line.split(":")[0].split(" down")[0].strip()
            if label and label != "Notable":
                notable.append(f"{label} down")
    if not notable:
        return "Notable: none"
    return "Notable: " + ", ".join(notable)


def _workouts_weekly_line(db_path: str, ref_date: str) -> str | None:
    """Delegates to workouts.weekly_line for the workouts summary."""
    from . import workouts
    return workouts.weekly_line(db_path, ref_date)


CARD_WINDOW_DAYS = RECENT_DAYS + BASELINE_DAYS  # 35: the full sparkline window shown on a tile


def _series_window(series: dict[str, float], ref_date: str, days: int = CARD_WINDOW_DAYS) -> list[float | None]:
    """`series` (a {date: value} dict) as a fixed-length list, oldest to newest, ending at
    ref_date -- a day with no value is None, so the sparkline can show real gaps rather than
    interpolating across them."""
    ref = Date.fromisoformat(ref_date)
    return [series.get((ref - timedelta(days=days - 1 - i)).isoformat()) for i in range(days)]


TREND_TILE_COLOR = "#4aa8e0"  # the design system's neutral "accent" -- deliberately not a
# concern-level color: for a trend report, up/down is not inherently good or bad (e.g. resting
# HR up is a concern, sleep up is not), so tiles never borrow the clinical red/amber/green scale.


def build_card_tiles(db_path: str, config: dict, ref_date: str) -> list[dict]:
    """Structured per-metric tiles for the weekly trend card: real daily series for the
    sparkline, the same 7d-vs-28d comparison `report()` already prints as text. At most 6 tiles
    (render_card's own cap); BP first (most clinically salient), then the configured metrics in
    METRIC_LABELS order, skipping any with no data at all."""
    chi = ZoneInfo(config.get("timezone", settings.timezone_name()))
    metrics_cfg = config.get("metrics") or {}
    tiles: list[dict] = []

    readings = bp_readings(db_path)
    if readings:
        by_date: dict[str, tuple[float, float]] = {}
        for d, _t, sys_v, dia_v in readings:
            by_date[d] = (sys_v, dia_v)
        sys_series = {d: v[0] for d, v in by_date.items()}
        recent_start = (Date.fromisoformat(ref_date) - timedelta(days=RECENT_DAYS - 1)).isoformat()
        baseline_start = (Date.fromisoformat(ref_date) - timedelta(days=RECENT_DAYS + BASELINE_DAYS - 1)).isoformat()
        recent_sys = [v for d, v in sys_series.items() if d >= recent_start]
        baseline_sys = [v for d, v in sys_series.items() if baseline_start <= d < recent_start]
        if recent_sys:
            recent_dia = [v[1] for d, v in by_date.items() if d >= recent_start]
            direction = _direction(st.mean(recent_sys), baseline_sys) if len(baseline_sys) >= MIN_BASELINE_DAYS else "flat"
            tiles.append({
                "label": "Blood pressure", "unit": "mmHg",
                "latest": f"{_fmt(st.mean(recent_sys), 0)}/{_fmt(st.mean(recent_dia), 0)}",
                "note": f"vs 28d {_fmt(st.median(baseline_sys), 0) if baseline_sys else 'n/a'}, {direction}",
                "values": _series_window(sys_series, ref_date), "band": None, "color": TREND_TILE_COLOR,
            })

    for tc, label, unit in METRIC_LABELS:
        if tc not in metrics_cfg or len(tiles) >= 6:
            continue
        series = _daily_values(db_path, config, tc, chi)
        if not series:
            continue
        dates = sorted(series)
        recent_start = (Date.fromisoformat(ref_date) - timedelta(days=RECENT_DAYS - 1)).isoformat()
        baseline_start = (Date.fromisoformat(ref_date) - timedelta(days=RECENT_DAYS + BASELINE_DAYS - 1)).isoformat()
        recent_vals = [series[d] for d in dates if d >= recent_start]
        baseline_vals = [series[d] for d in dates if baseline_start <= d < recent_start]
        if len(recent_vals) < MIN_RECENT_DAYS:
            continue
        recent_mean = st.mean(recent_vals)
        baseline_median = st.median(baseline_vals) if baseline_vals else 0.0
        direction = _direction(recent_mean, baseline_vals) if len(baseline_vals) >= MIN_BASELINE_DAYS else "flat"
        display_series = series
        if tc == "weight":
            latest = f"{_fmt(to_display_weight(recent_mean))} {weight_unit()}"
            baseline_median = to_display_weight(baseline_median)
            display_series = {d: to_display_weight(v) for d, v in series.items()}
        elif tc == "oxygen_saturation":
            pct = recent_mean * 100
            # Known data-quality issue (flagged 2026-09-22): SpO2 samples are stored as a mix of
            # 0-1 fractions and 0-100 percents in the same table, so an unguarded *100 can produce
            # a physiologically impossible number (seen live: "5552.8%"). The text report()
            # doesn't have this guard yet (out of scope here -- a card headline in 30px bold makes
            # a bad aggregate far more visible than one buried line, so the card gets the guard
            # first); real fix is normalizing units at ingestion. Skip the tile rather than show it.
            if not (50 <= pct <= 100):
                continue
            latest = f"{_fmt(pct)}%"
            baseline_median *= 100
            display_series = {d: v * 100 for d, v in series.items()}
        else:
            latest = f"{_fmt(recent_mean)} {unit}"
        tiles.append({
            "label": label, "unit": unit, "latest": latest,
            "note": f"vs 28d {_fmt(baseline_median)} {unit}, {direction}",
            "values": _series_window(display_series, ref_date), "band": None, "color": TREND_TILE_COLOR,
        })
    return tiles[:6]


def report(db_path: str, config: dict, ref_date: str) -> list[str]:
    """Build the weekly report lines for the Chicago day ``ref_date`` (YYYY-MM-DD)."""
    ref = Date.fromisoformat(ref_date)
    chi = ZoneInfo(config.get("timezone", settings.timezone_name()))
    recent_start = (ref - timedelta(days=RECENT_DAYS - 1)).isoformat()
    start_label = ref - timedelta(days=RECENT_DAYS - 1)
    end_label = ref
    header = f"Weekly health {start_label:%Y-%m-%d} to {end_label:%Y-%m-%d} ({settings.tz_label()})"

    # Empty database -> one clear line, exit 0.
    conn = _open(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM samples")
    count = cur.fetchone()[0]
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sleep_sessions'")
    if cur.fetchone() is not None:
        cur.execute("SELECT COUNT(*) FROM sleep_sessions")
        count += cur.fetchone()[0]
    conn.close()
    if count == 0:
        return [f"{header}: no data"]

    metrics_cfg = config.get("metrics") or {}
    lines: list[str] = [header]
    for tc, label, unit in METRIC_LABELS:
        if tc not in metrics_cfg:
            continue
        series = _daily_values(db_path, config, tc, chi)
        lines.append(_metric_line(tc, label, unit, ref_date, series))

    bp = _bp_line(db_path, ref_date)
    if bp:
        lines.append(bp)

    # Workouts weekly line, inserted directly before the Notable section.
    wk = _workouts_weekly_line(db_path, ref_date)
    if wk:
        lines.append(wk)

    lines.append(_notable(lines))
    lines.append("Informational, not medical advice.")
    return lines


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="weekly", description="Weekly health trends report.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--config", default=str(settings.package_data("health_monitor.yaml")))
    args = parser.parse_args()

    import yaml
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for line in report(args.db, cfg, args.date):
        print(line)
