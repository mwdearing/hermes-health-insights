"""
Core analysis: daily aggregates, 30/90-day baselines, trend slopes,
coverage and freshness. Emits a stable JSON document (schema v1) plus a
short Markdown report.

Contract v1 document:
{
  "schemaVersion": 1,
  "generatedAt": "<ISO8601 UTC>",
  "generatedAtMs": <epoch>,
  "metricWindowDays": 30,
  "baselineWindowDays": 90,
  "metrics": {
    "<type_code>": {
      "metric": "<display name>",
      "healthkit": "<HK identifier or null>",
      "unit": "<unit>",
      "minDate": "<ISO>",
      "maxDate": "<ISO>",
      "count": <int>,
      "daily": [
        {"date": "YYYY-MM-DD", "count": <int>, "mean": <float|null>, "median": <float|null>, "robustZmin": <float|null>, "robustZmax": <float|null>}
      ],
      "baseline": {
        "30d": {"count": <int>, "median": <float|null>, "mad": <float|null>, "robustStd": <float|null>, "min": <float|null>, "max": <float|null>},
        "90d": {...}
      },
      "trend": {"days": <int>, "slopePerDay": <float|null>},
      "coverage": {"days": <int>, "daysWithData": <int>, "pct": <float>},
      "freshness": {"lastSampleDate": "<ISO>"||null, "daysSince": <int|null>, "status": "ok|stale|missing"}
    }
  }
}
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from . import health_types
from . import stats as st
from .bridge_db import Sample, SyncRun, _date_key, CHICAGO


DEFAULT_METRIC_WINDOW_DAYS = 30
DEFAULT_BASELINE_WINDOW_DAYS = 90
MIN_HISTORY_DAYS = 14  # below this, a type is flagged as insufficient history


@dataclass
class DailyRow:
    date: str
    count: int
    mean: Optional[float] = None
    median: Optional[float] = None
    robustZmin: Optional[float] = None
    robustZmax: Optional[float] = None


@dataclass
class Baseline:
    count: int
    median: Optional[float] = None
    mad: Optional[float] = None
    robustStd: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None


@dataclass
class MetricReport:
    type_code: str
    metric: str
    healthkit: Optional[str]
    unit: str
    min_date: str
    max_date: str
    count: int
    daily: list[DailyRow]
    baseline_30d: Optional[Baseline]
    baseline_90d: Optional[Baseline]
    trend_slope: Optional[float]
    trend_days: int
    coverage_days: int
    coverage_days_with_data: int
    total_days_with_data: int
    freshness_last_date: Optional[str]
    freshness_days_since: Optional[int]
    freshness_status: str


def _days_between(start: str, end: str) -> int:
    """Integer number of days between two ISO date strings (inclusive-ish)."""
    try:
        d0 = datetime.fromisoformat(start[:10])
        d1 = datetime.fromisoformat(end[:10])
    except (ValueError, TypeError):
        return 0
    return max(0, (d1 - d0).days + 1)


def _baseline(values: list[float], window_days: int) -> Optional[Baseline]:
    if not values:
        return None
    return Baseline(
        count=len(values),
        median=st.median(values),
        mad=st.mad(values),
        robustStd=st.robust_std(values),
        min=min(values),
        max=max(values),
    )


def _trend(values_by_date: dict[str, list[float]], window_days: int) -> tuple[Optional[float], int]:
    """Least-squares slope per day over the most recent window_days of daily means."""
    dates = sorted(values_by_date.keys())
    if len(dates) < 2:
        return None, len(dates)
    cutoff = dates[max(0, len(dates) - window_days)] if window_days > 0 else dates[0]
    recent = [d for d in dates if d >= cutoff]
    if len(recent) < 2:
        recent = dates
    xs = [float(datetime.fromisoformat(d[:10]).replace(tzinfo=timezone.utc).timestamp()) for d in recent]
    ys = [st.mean(values_by_date[d]) for d in recent]
    # st.slope() works on unix-seconds x, so it returns slope-per-second.
    # The recent dates span exactly span_days days, so multiply by 86400 to
    # convert to a per-day slope (no further normalization needed).
    slope = st.slope(xs, ys) * 86400
    return slope, len(recent)


def _freshness(max_date: str, now: datetime) -> tuple[Optional[str], Optional[int], str]:
    """Return (last_sample_date, days_since, status).

    max_date is a Chicago calendar date; compare against the Chicago now so the
    difference counts Chicago days, not UTC days.
    """
    if not max_date:
        return None, None, "missing"
    try:
        last = datetime.fromisoformat(max_date[:10]).replace(tzinfo=CHICAGO)
    except (ValueError, TypeError):
        return max_date, None, "unknown"
    days = (now.astimezone(CHICAGO) - last).days
    if days > 3:
        status = "stale"
    else:
        status = "ok"
    return max_date, days, status


def _build_metric(type_code: str, samples: list[Sample], sync_runs: list[SyncRun], now: datetime, window_days: int, baseline_days: int) -> MetricReport:
    metric = health_types.metric_name(type_code)
    healthkit = health_types.healthkit_identifier(type_code)

    # Group by date.
    values_by_date: dict[str, list[float]] = defaultdict(list)
    for s in samples:
        d = _date_key(s.start)
        if d:
            values_by_date[d].append(s.value)

    dates = sorted(values_by_date.keys())
    if not dates:
        # No samples for this type.
        return MetricReport(
            type_code=type_code, metric=metric, healthkit=healthkit, unit="",
            min_date="", max_date="", count=0, daily=[],
            baseline_30d=None, baseline_90d=None,
            trend_slope=None, trend_days=0,
            coverage_days=0, coverage_days_with_data=0,
            total_days_with_data=0,
            freshness_last_date=None, freshness_days_since=None, freshness_status="missing",
        )

    unit = samples[0].unit
    min_date = dates[0]
    max_date = dates[-1]
    count = sum(len(v) for v in values_by_date.values())

    # Daily rows: mean, median, robust z bounds.
    daily: list[DailyRow] = []
    all_values = [v for vs in values_by_date.values() for v in vs]
    med = st.median(all_values)
    rstd = st.robust_std(all_values)
    for d in dates:
        vals = values_by_date[d]
        row = DailyRow(date=d, count=len(vals), mean=st.mean(vals), median=st.median(vals))
        if rstd and rstd > 0:
            row.robustZmin = (min(vals) - med) / rstd
            row.robustZmax = (max(vals) - med) / rstd
        daily.append(row)

    # Baselines. Dates are Chicago day keys, so window cutoffs are computed
    # from the Chicago calendar date, not UTC.
    now_chi = now.astimezone(CHICAGO)
    cutoff_30 = (now_chi - timedelta(days=window_days)).strftime("%Y-%m-%d")
    cutoff_90 = (now_chi - timedelta(days=baseline_days)).strftime("%Y-%m-%d")
    recent_30 = [d for d in dates if d >= cutoff_30]
    recent_90 = [d for d in dates if d >= cutoff_90]
    baseline_30d = _baseline([v for d in recent_30 for v in values_by_date[d]], window_days)
    baseline_90d = _baseline([v for d in recent_90 for v in values_by_date[d]], baseline_days)

    # Trend over the metric window.
    slope, trend_days = _trend(values_by_date, window_days)

    # Coverage: how many days in the window have data (Chicago day keys).
    window_start = (now_chi - timedelta(days=window_days)).strftime("%Y-%m-%d")
    coverage_days_with_data = sum(1 for d in dates if d >= window_start)
    total_days_with_data = len(dates)
    coverage_days = max(1, window_days)

    # Freshness from latest sample.
    last_date, days_since, status = _freshness(max_date, now)

    return MetricReport(
        type_code=type_code, metric=metric, healthkit=healthkit, unit=unit,
        min_date=min_date, max_date=max_date, count=count, daily=daily,
        baseline_30d=baseline_30d, baseline_90d=baseline_90d,
        trend_slope=slope, trend_days=trend_days,
        coverage_days=coverage_days, coverage_days_with_data=coverage_days_with_data,
        total_days_with_data=total_days_with_data,
        freshness_last_date=last_date, freshness_days_since=days_since, freshness_status=status,
    )


def build_report(db_path: str, type_codes: Optional[set[str]] = None, window_days: int = DEFAULT_METRIC_WINDOW_DAYS, baseline_days: int = DEFAULT_BASELINE_WINDOW_DAYS, now: Optional[datetime] = None) -> dict:
    """
    Build the full metric report JSON-serializable dict from a bridge DB.

    `now` defaults to the current UTC time.
    """
    import sqlite3
    from .bridge_db import open_readonly, read_samples, read_sync_runs, list_type_codes

    conn = open_readonly(db_path)
    try:
        if type_codes is None:
            type_codes = set(list_type_codes(conn))
        sync_runs = read_sync_runs(conn)
        samples = read_samples(conn, type_codes)
    finally:
        conn.close()

    now = now or datetime.now(timezone.utc)
    metrics: dict[str, dict] = {}
    for tc in sorted(type_codes):
        tc_samples = [s for s in samples if s.type_code == tc]
        rep = _build_metric(tc, tc_samples, sync_runs, now, window_days, baseline_days)
        metrics[tc] = _metric_to_dict(rep)

    return {
        "schemaVersion": 1,
        "generatedAt": now.isoformat(),
        "generatedAtMs": int(now.timestamp()),
        "metricWindowDays": window_days,
        "baselineWindowDays": baseline_days,
        "metrics": metrics,
    }


def _metric_to_dict(rep: MetricReport) -> dict:
    out: dict = {
        "metric": rep.metric,
        "healthkit": rep.healthkit,
        "unit": rep.unit,
        "minDate": rep.min_date,
        "maxDate": rep.max_date,
        "count": rep.count,
        "daily": [
            {
                "date": d.date,
                "count": d.count,
                "mean": round(d.mean, 4) if d.mean is not None else None,
                "median": round(d.median, 4) if d.median is not None else None,
                "robustZmin": round(d.robustZmin, 3) if d.robustZmin is not None else None,
                "robustZmax": round(d.robustZmax, 3) if d.robustZmax is not None else None,
            }
            for d in rep.daily
        ],
        "baseline": {
            "30d": _baseline_to_dict(rep.baseline_30d),
            "90d": _baseline_to_dict(rep.baseline_90d),
        },
        "trend": {
            "days": rep.trend_days,
            "slopePerDay": round(rep.trend_slope, 6) if rep.trend_slope is not None else None,
        },
        "coverage": {
            "days": rep.coverage_days,
            "daysWithData": rep.coverage_days_with_data,
            "totalDaysWithData": rep.total_days_with_data,
            "pct": round(rep.coverage_days_with_data / rep.coverage_days * 100, 1) if rep.coverage_days else 0.0,
        },
        "freshness": {
            "lastSampleDate": rep.freshness_last_date,
            "daysSince": rep.freshness_days_since,
            "status": rep.freshness_status,
        },
    }
    return out


def _baseline_to_dict(b: Optional[Baseline]) -> Optional[dict]:
    if b is None:
        return None
    return {
        "count": b.count,
        "median": round(b.median, 4) if b.median is not None else None,
        "mad": round(b.mad, 4) if b.mad is not None else None,
        "robustStd": round(b.robustStd, 4) if b.robustStd is not None else None,
        "min": round(b.min, 4) if b.min is not None else None,
        "max": round(b.max, 4) if b.max is not None else None,
    }


def markdown_report(report: dict) -> str:
    """Render a short Markdown report of the metric report. Aggregates only."""
    lines = [
        "# Health Insights",
        "",
        f"Generated: {report.get('generatedAt', 'n/a')}",
        f"Metric window: {report.get('metricWindowDays', 30)}d | Baseline window: {report.get('baselineWindowDays', 90)}d",
        "",
    ]
    metrics = report.get("metrics", {})
    lines.append(f"**{len(metrics)} metrics analyzed**")
    lines.append("")
    lines.append("| Metric | Unit | Count | 30d Median | 30d MAD | Trend/day | Coverage | Last | Status |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for tc in sorted(metrics):
        m = metrics[tc]
        b30 = m.get("baseline", {}).get("30d") or {}
        cov = m.get("coverage", {})
        fr = m.get("freshness", {})
        tr = m.get("trend", {})
        med = b30.get("median")
        mad = b30.get("mad")
        slope = tr.get("slopePerDay")
        lines.append(
            f"| {m['metric']} | {m['unit']} | {m['count']} | "
            f"{_fmt(med)} | {_fmt(mad)} | {_fmt(slope)} | "
            f"{cov.get('pct', 0)}% ({cov.get('daysWithData', 0)}/{cov.get('days', 0)}) | "
            f"{fr.get('daysSince', 'n/a')}d | {fr.get('status', 'unknown')} |"
        )
    lines.append("")
    # Insufficient history flag: based on the total number of distinct days
    # with data across all history, not the first-to-last span. A type with two
    # readings 100 days apart has a 100-day span but only two days of data, so
    # span alone would wrongly pass it. total_days_with_data is the honest
    # measure of history depth.
    window = report.get("metricWindowDays", DEFAULT_METRIC_WINDOW_DAYS)
    insufficient = [
        (m["metric"], m["minDate"], m["maxDate"], m["coverage"]["totalDaysWithData"])
        for m in metrics.values()
        if m["count"] > 0 and m["coverage"]["totalDaysWithData"] < MIN_HISTORY_DAYS
    ]
    if insufficient:
        lines.append(f"**Insufficient history (< {MIN_HISTORY_DAYS} days with data):**")
        for name, mn, mx, ndays in insufficient:
            lines.append(f"- {name}: {mn} to {mx} ({ndays} day(s) with data)")
        lines.append("")
    return "\n".join(lines)


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)
