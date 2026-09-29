"""
Read bridge SQLite snapshots with stdlib sqlite3.

The bridge stores Apple Health data in a single SQLite DB. This module opens
it read-only and extracts per-sample rows plus the sync history needed for
coverage and freshness. It never writes to the source DB.

Contract (bridge schema):
  samples(sample_id, source_id, type_code, client_record_id, start_time,
          end_time, value, unit, metadata_json, ...)
  sync_runs(sync_run_id, started_at, finished_at, status, sample_count, ...)
  sources(source_id, source_key, name, kind, ...)

start_time/end_time are ISO 8601 (UTC 'Z' or offset). We normalize to a
common UTC form for grouping and comparison.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from health_insights import settings

# Apple Health days run on the user's local calendar. Every day boundary (daily
# buckets, "yesterday", baselines, digests) uses the configured timezone (see
# settings.timezone), never the UTC date and never a guessed fixed offset. A late-night reading in
# Chicago (23:30 CDT) must land on the same day as its 04:30 UTC successor.
CHICAGO = settings.timezone()


@dataclass
class Sample:
    type_code: str
    start: str
    end: str
    value: float
    unit: str
    source_key: str


@dataclass
class SyncRun:
    started_at: str
    finished_at: str
    status: str
    sample_count: int


def _normalize_ts(ts: Optional[str]) -> Optional[str]:
    """Normalize an ISO 8601 timestamp to a comparable UTC string.

    Accepts 'Z' suffix, '+HH:MM' offsets, or naive datetimes. Returns None
    when the value is empty or unparseable.
    """
    if not ts:
        return None
    s = ts.strip()
    if not s:
        return None
    # Normalize trailing Z to +00:00 for fromisoformat on older Pythons.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _date_key(ts: Optional[str]) -> Optional[str]:
    """Return the Chicago calendar date (YYYY-MM-DD) for a timestamp, or None."""
    norm = _normalize_ts(ts)
    if not norm:
        return None
    dt = datetime.fromisoformat(norm).astimezone(CHICAGO)
    return dt.strftime("%Y-%m-%d")


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open a bridge SQLite DB read-only."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def list_type_codes(conn: sqlite3.Connection) -> list[str]:
    """Return the distinct type_codes present in the samples table."""
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT type_code FROM samples "
        "WHERE type_code IS NOT NULL AND type_code != '' ORDER BY type_code"
    )
    return [row[0] for row in cur.fetchall()]


def read_samples(conn: sqlite3.Connection, type_codes: Optional[set[str]] = None) -> list[Sample]:
    """Read sample rows, optionally filtered to a set of type_codes."""
    cur = conn.cursor()
    if type_codes:
        placeholders = ",".join("?" * len(type_codes))
        cur.execute(
            f"SELECT type_code, start_time, end_time, value, unit, source_id "
            f"FROM samples WHERE type_code IN ({placeholders})",
            list(type_codes),
        )
    else:
        cur.execute(
            "SELECT type_code, start_time, end_time, value, unit, source_id FROM samples"
        )
    rows = cur.fetchall()
    samples: list[Sample] = []
    for type_code, start_time, end_time, value, unit, source_id in rows:
        if value is None:
            continue
        source_key = _source_key(conn, source_id)
        samples.append(
            Sample(
                type_code=type_code,
                start=_normalize_ts(start_time) or start_time or "",
                end=_normalize_ts(end_time) or end_time or "",
                value=float(value),
                unit=unit or "",
                source_key=source_key,
            )
        )
    return samples


def read_sync_runs(conn: sqlite3.Connection) -> list[SyncRun]:
    """Read sync_runs for coverage/freshness analysis."""
    cur = conn.cursor()
    cur.execute(
        "SELECT started_at, finished_at, status, sample_count "
        "FROM sync_runs ORDER BY started_at"
    )
    runs: list[SyncRun] = []
    for started_at, finished_at, status, sample_count in cur.fetchall():
        runs.append(SyncRun(started_at, finished_at, status or "", sample_count or 0))
    return runs


def _source_key(conn: sqlite3.Connection, source_id) -> str:
    """Look up a source's key by id; fall back to the id when not found."""
    cur = conn.cursor()
    cur.execute("SELECT source_key FROM sources WHERE source_id = ?", (source_id,))
    row = cur.fetchone()
    return row[0] if row and row[0] else str(source_id)
