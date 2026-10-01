from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from health_insights.hydration import digest_line


def _make_db(tmp_path: Path, rows: list[tuple[str, str, float, str]]) -> str:
    """Create a small SQLite DB with a samples table and return its path."""
    db = tmp_path / "test.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE samples (
            type_code TEXT,
            start_time TEXT,
            value REAL,
            unit TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO samples (type_code, start_time, value, unit) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(db)


def test_chicago_day_sum(tmp_path: Path):
    """A 200 mL entry at 23:30 CDT on Sep 29 has start_time on Sep 30 UTC,
    but should still count for Sep 29 Chicago."""
    db = _make_db(tmp_path, [
        ("hydration", "2026-09-30T04:30:00+00:00", 200, "mL"),
    ])
    result = digest_line(db, "2026-09-29")
    assert result is not None
    assert "2026-09-29" in result or "Sep 29" in result
    assert "0.2 L" in result


def test_before_midnight_previous_day(tmp_path: Path):
    """An entry at 23:55 CDT on Sep 29 belongs to Sep 29, not Sep 30."""
    db = _make_db(tmp_path, [
        ("hydration", "2026-09-30T04:55:00+00:00", 500, "mL"),
    ])
    result29 = digest_line(db, "2026-09-29")
    result30 = digest_line(db, "2026-09-30")
    assert result29 is not None
    assert "0.5 L" in result29
    assert result30 is not None
    assert "none logged" in result30


def test_other_type_codes_ignored(tmp_path: Path):
    """Non-hydration rows should be ignored."""
    db = _make_db(tmp_path, [
        ("heart_rate", "2026-09-29T12:00:00+00:00", 72, "bpm"),
        ("weight", "2026-09-29T08:00:00+00:00", 80, "kg"),
    ])
    result = digest_line(db, "2026-09-29")
    assert result is not None
    assert "none logged" in result


def test_no_water_none_logged(tmp_path: Path):
    """A DB with only unrelated data shows 'none logged'."""
    db = _make_db(tmp_path, [
        ("sleep_analysis", "2026-09-29T07:00:00+00:00", 7.5, "h"),
    ])
    result = digest_line(db, "2026-09-29")
    assert result is not None
    assert "none logged" in result


def test_missing_table_returns_none(tmp_path: Path):
    """A DB without a samples table returns None."""
    db = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.commit()
    conn.close()
    assert digest_line(str(db), "2026-09-29") is None
