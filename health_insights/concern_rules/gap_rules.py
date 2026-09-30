"""Data-gap concern rule.

Returns a single Finding when one or more configured metrics have gone
silent for more than three Chicago calendar days before *ref_date*.

Reuses ``weekly.METRIC_LABELS`` for (type_code, label, unit) tuples and
``bridge_db._normalize_ts`` for timestamp normalisation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from ..bridge_db import _normalize_ts
from ..weekly import METRIC_LABELS
from health_insights import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict[str, Any]:
    """Return the health_monitor config dict."""
    _pkg = Path(__file__).resolve().parent.parent.parent
    _cfg_path = settings.package_data("health_monitor.yaml")
    with open(_cfg_path, "r") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def data_gap_findings(
    db_path: str,
    ref_date,
    config: Optional[dict[str, Any]] = None,
) -> list["Finding"]:
    """Return a single data-gap Finding when metrics are silent > 3 days.

    *ref_date* may be a date object or a "YYYY-MM-DD" string.
    When *config* is None the real default config is loaded.
    """
    from ..concerns import Finding  # late import to avoid circular import

    if config is None:
        config = _load_config()

    metrics_cfg = config.get("metrics") or {}
    chi = config.get("timezone", settings.timezone_name())
    from zoneinfo import ZoneInfo

    _zone = ZoneInfo(chi)

    # ref_date as date object
    if isinstance(ref_date, str):
        _ref = Date.fromisoformat(ref_date)
    else:
        _ref = ref_date

    # Gather distinct Chicago calendar dates per type_code
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return []

    try:
        # Check if samples table exists
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='samples'")
        if cur.fetchone() is None:
            return []

        # Get distinct type_codes
        cur.execute(
            "SELECT DISTINCT type_code FROM samples "
            "WHERE type_code IS NOT NULL AND type_code != ''"
        )
        present_type_codes = {row[0] for row in cur.fetchall()}

        # For each configured metric that is present, get distinct dates
        silent_metrics: list[tuple[str, int]] = []  # (label, day_count)

        for tc, label, unit in METRIC_LABELS:
            if tc not in metrics_cfg:
                continue
            if tc not in present_type_codes:
                # Never tracked — not a gap, just untracked
                continue

            # Get distinct dates for this type_code
            cur.execute(
                "SELECT DISTINCT start_time FROM samples "
                "WHERE type_code = ? AND start_time IS NOT NULL AND start_time != ''",
                (tc,),
            )
            date_set: set[str] = set()
            for (ts,) in cur.fetchall():
                norm = _normalize_ts(ts)
                if not norm:
                    continue
                from datetime import datetime
                dt = datetime.fromisoformat(norm).astimezone(_zone)
                date_set.add(dt.strftime("%Y-%m-%d"))

            # Skip if fewer than 14 distinct days total
            if len(date_set) < 14:
                continue

            # Find the most recent date
            sorted_dates = sorted(date_set)
            most_recent = sorted_dates[-1]
            most_recent_date = Date.fromisoformat(most_recent)

            # If more than 3 days before ref_date, it's silent
            day_gap = (_ref - most_recent_date).days
            if day_gap > 3:
                silent_metrics.append((label, day_gap))

    finally:
        conn.close()

    if not silent_metrics:
        return []

    # Build evidence string
    evidence_parts = [f"{label} not seen in {n} days" for label, n in silent_metrics]
    evidence = "; ".join(evidence_parts)

    return [
        Finding(
            id="data_gap",
            level=1,
            title="Some health data has gone quiet",
            evidence=evidence,
            source="heuristic",
            advice=(
                "Could just be the watch or phone not syncing; "
                "check if you'd expect data."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Store-level staleness
# ---------------------------------------------------------------------------

STALE_AFTER_DAYS = 3


def store_staleness_findings(db_path: str, ref_date) -> list["Finding"]:
    """Level-2 finding when the newest record of ANY kind is older than three days.

    Independent of the per-metric 14-day rule: fires for any database that has at
    least one sample, workout or sleep session, however short its history.
    """
    import sqlite3
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ..concerns import Finding  # late import to avoid circular import

    _ref = Date.fromisoformat(ref_date) if isinstance(ref_date, str) else ref_date
    zone = ZoneInfo(settings.timezone_name())

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "samples" not in tables:
            return []
        newest: Optional[datetime] = None
        for table in ("samples", "workouts", "sleep_sessions"):
            if table not in tables:
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "start_time" not in cols:
                continue
            row = conn.execute(
                f"SELECT MAX(start_time) FROM {table} WHERE start_time IS NOT NULL AND start_time != ''"
            ).fetchone()
            norm = _normalize_ts(row[0]) if row else None
            if not norm:
                continue
            dt = datetime.fromisoformat(norm)
            if newest is None or dt > newest:
                newest = dt
    finally:
        conn.close()

    if newest is None:
        return []
    newest_date = newest.astimezone(zone).date()
    age = (_ref - newest_date).days
    if age <= STALE_AFTER_DAYS:
        return []
    return [
        Finding(
            id="store_stale",
            level=2,
            title=f"No new health data since {newest_date.isoformat()}",
            evidence=f"newest record {age} days old",
            source="heuristic",
            advice=(
                "Check that the phone app is still syncing and that the receiver is running; "
                "until data flows again, the other checks only see old records."
            ),
        )
    ]
