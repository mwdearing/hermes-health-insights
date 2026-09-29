"""
Tests for the health_insights library (synthetic fixtures only).

Builds a tiny fake bridge SQLite DB in a temp dir with known samples and
asserts the coverage/freshness/baseline/trend outputs. No real health data.

Run: python -m pytest -q
     or: python tests/test_health_insights.py
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from health_insights import analysis, bridge_db, stats


def _make_fake_db(path: Path) -> None:
    """Create a fake bridge DB with:
      - weight: 20 daily samples over 20 days (good history)
      - steps: 3 samples over 2 days (insufficient history)
      - resting_heart_rate: 1 sample 5 days ago (stale)
      - heart_rate_variability_sdnn: 10 samples over 10 days (trend up)
    """
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE samples ("
        "sample_id INTEGER PRIMARY KEY, source_id INTEGER, type_code TEXT,"
        "client_record_id TEXT, start_time TEXT, end_time TEXT,"
        "value REAL, unit TEXT, metadata_json TEXT)"
    )
    cur.execute(
        "CREATE TABLE sources (source_id INTEGER, source_key TEXT, name TEXT, kind TEXT)"
    )
    cur.execute(
        "CREATE TABLE sync_runs (sync_run_id INTEGER, started_at TEXT, "
        "finished_at TEXT, status TEXT, sample_count INTEGER)"
    )
    cur.execute("INSERT INTO sources VALUES (1, 'test.phone', 'Test Phone', 'phone')")

    def add(type_code: str, days_ago: int, value: float, unit: str, n: int = 1):
        for i in range(n):
            dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=i)
            start = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (type_code, f"{type_code}-{i}-{i}", start, start, f"{value + i}", unit),
            )

    # weight: 20 days, value ~70 with slight upward drift
    for i in range(20):
        add("weight", i, 70.0 + i * 0.05, "kg")
    # steps: 3 samples over 2 days (insufficient history)
    add("steps", 0, 8000, "count", n=2)
    add("steps", 1, 7000, "count", n=1)
    # resting_heart_rate: 1 sample 5 days ago (stale)
    add("resting_heart_rate", 5, 55, "bpm")
    # HRV: 10 days, value increasing over time (today's value is highest).
    for i in range(10):
        add("heart_rate_variability_sdnn", i, 40.0 + (9 - i) * 2.0, "ms")
    # sparse_span: 2 readings 100 days apart. Span is 101 days (>= 14), so the
    # old span-based logic would have wrongly passed it. total_days_with_data is
    # only 2 (< 14), so it must still be flagged. This is the exact bug the user
    # reported: span-based flags hide sparse-but-long history.
    add("sparse_span", 100, 10.0, "count")
    add("sparse_span", 0, 11.0, "count")

    con.commit()
    con.close()


def _load_report(db_path: str, now: datetime):
    return analysis.build_report(db_path, now=now)


def test_stats():
    assert stats.mean([1, 2, 3, 4]) == 2.5
    assert stats.median([1, 2, 3]) == 2
    assert stats.median([]) == 0.0
    assert stats.mad([2, 2, 2]) == 0.0
    # robust std is 1.4826 * MAD
    vals = [1.0, 2.0, 3.0, 4.0]
    med = stats.median(vals)
    expected = 1.4826 * stats.mad(vals)
    assert abs(stats.robust_std(vals) - expected) < 1e-9
    # slope of a perfect line y = 2x + 1
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [1.0, 3.0, 5.0, 7.0]
    slope = stats.slope(xs, ys)
    assert abs(slope - 2.0) < 1e-9
    # slope of constant y is 0
    assert stats.slope(xs, [5.0, 5.0, 5.0, 5.0]) == 0.0


def test_coverage_and_freshness():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)

        metrics = report["metrics"]
        # weight: 20 days of data, span >= 14 days
        w = metrics["weight"]
        assert w["count"] == 20
        assert w["freshness"]["status"] == "ok"
        assert w["freshness"]["daysSince"] == 0
        assert w["coverage"]["daysWithData"] == 20

        # steps: only 2 days span -> insufficient history
        s = metrics["steps"]
        assert s["count"] == 3
        span = analysis._days_between(s["minDate"], s["maxDate"])
        assert span < 14

        # resting_heart_rate: 1 sample 5 days ago -> stale
        r = metrics["resting_heart_rate"]
        assert r["freshness"]["status"] == "stale"
        assert r["freshness"]["daysSince"] == 5

        # HRV: 10 samples over 10 days
        h = metrics["heart_rate_variability_sdnn"]
        assert h["count"] == 10


def test_baseline():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)
        w = report["metrics"]["weight"]
        b30 = w["baseline"]["30d"]
        assert b30["count"] == 20
        # median should be near 70
        assert 68 < b30["median"] < 72
        assert b30["mad"] >= 0
        # 90d baseline should also have data
        assert w["baseline"]["90d"]["count"] > 0


def test_trend_positive():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)
        h = report["metrics"]["heart_rate_variability_sdnn"]
        # HRV values increase over time -> positive slope/day
        assert h["trend"]["slopePerDay"] is not None
        assert h["trend"]["slopePerDay"] > 0
        # Unit check: the fixture increases by 2/day, so slopePerDay must be
        # a per-day value (roughly +2.0), not a per-second value (~1e-6).
        assert h["trend"]["slopePerDay"] > 1.0


def test_insufficient_history_flag():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)
        md = analysis.markdown_report(report)
        # steps should be flagged as insufficient history
        assert "Insufficient history" in md
        assert "Steps" in md


def test_insufficient_history_uses_days_with_data_not_span():
    # A type whose readings span 100+ days but only exist on 2 distinct days
    # must still be flagged as insufficient history. Span alone would wrongly
    # pass it.
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)
        m = report["metrics"]["sparse_span"]
        # Span is large (>= 14 days) but total distinct days with data is small
        # (< 14). The old span-based logic would have wrongly passed this.
        assert analysis._days_between(m["minDate"], m["maxDate"]) >= 14
        assert m["coverage"]["totalDaysWithData"] < analysis.MIN_HISTORY_DAYS
        # The flag must still fire.
        md = analysis.markdown_report(report)
        assert "Insufficient history" in md
        assert "sparse_span" in md


def test_daily_aggregates():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)
        w = report["metrics"]["weight"]
        # daily should have entries for each day with data
        assert len(w["daily"]) == 20
        assert w["daily"][0]["count"] == 1
        assert w["daily"][0]["mean"] is not None


def test_json_serializable():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        report = _load_report(str(db), now)
        # Should not raise
        json.dumps(report)


if __name__ == "__main__":
    # Support running without pytest.
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print("\nAll tests passed")
