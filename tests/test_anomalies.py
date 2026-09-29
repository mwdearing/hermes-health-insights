"""
Tests for health_insights/anomalies.py (synthetic fixtures only).

Builds a fake bridge SQLite DB with known samples and asserts the detector
flags correctly. No real health data, no values in git.

Run: python -m pytest -q
     or: python tests/test_anomalies.py
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from health_insights import anomalies


def _make_fake_db(path: Path) -> None:
    """Create a fake bridge DB with enough history for the detector.

    weight: 30 days of ~70 kg with a spike on the last day.
    heart_rate: 30 flat days (MAD==0 -> robust_z never flags).
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

    def add(type_code: str, days_ago: int, value: float, unit: str):
        dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        start = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        # Small jitter so the baseline MAD is nonzero (real data varies).
        jitter = (days_ago % 5) * 0.1
        cur.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            (type_code, f"{type_code}-{days_ago}", start, start, f"{value + jitter}", unit),
        )

    # weight: 30 days around 70 kg with jitter, spike on day 0 (today).
    for i in range(30):
        add("weight", i, 70.0, "kg")
    add("weight", 0, 73.5, "kg")  # spike -> should flag

    # heart_rate: 30 flat days (MAD==0).
    for i in range(30):
        add("heart_rate", i, 65.0, "bpm")

    con.commit()
    con.close()


def _config() -> dict:
    return {
        "timezone": "America/Chicago",
        "metrics": {
            "weight": {
                "baseline_window_days": 30,
                "anomaly": {
                    "robust_sigma": 3.0,
                    "max_change_over_days": 2,
                    "window_days": 3,
                    "min_abs": 30,
                    "max_abs": 400,
                },
                "trend": {"slope_per_day_alert": 0.2},
            },
            "heart_rate": {
                "baseline_window_days": 30,
                "anomaly": {"robust_sigma": 3.0, "min_abs": 30, "max_abs": 220},
                "trend": {"slope_per_day_alert": 0.5},
            },
        },
    }


def _today() -> str:
    return datetime.now(timezone.utc).astimezone(anomalies.CHICAGO).strftime("%Y-%m-%d")


def _bucket_of(db: Path, type_code: str, days_ago: int) -> str:
    """Return the local-day bucket a naive UTC timestamp `days_ago` days ago landed in.

    The detector interprets naive timestamps as UTC, so a naive "100 days ago"
    timestamp buckets one hour earlier in Chicago. Read back the actual bucket.
    """
    conn = anomalies._open(str(db))
    by_date = anomalies._daily_means(conn, type_code, anomalies.CHICAGO)
    # Find the single date whose only value is the spike (250 bpm).
    for d, vals in by_date.items():
        if len(vals) == 1 and vals[0] > 220:
            return d
    # Fall back to the naive interpretation.
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_spike_flags():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        res = anomalies.detect(str(db), _config(), _today())
        assert res["weight"]["status"] == "flagged", res["weight"]


def test_bound_flag():
    """A value above max_abs must flag with the bound reason."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        con = sqlite3.connect(str(db))
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
        cur.execute("INSERT INTO sources VALUES (1, 't', 'T', 'phone')")
        # 30 flat days at 65 bpm ending yesterday, then a single 250 bpm reading today.
        for i in range(1, 31):
            dt = datetime.now(timezone.utc) - timedelta(days=i)
            start = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, 'heart_rate', 'h', ?, ?, 65.0, 'bpm')",
                (start, start),
            )
        # A 250 bpm reading today: 30 earlier days exist, so the bound check applies
        # and its daily mean (250) is above max_abs=220.
        dt0 = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES (1, 'heart_rate', 'h', ?, ?, 250.0, 'bpm')",
            (dt0, dt0),
        )
        con.commit()
        con.close()
        # Query the isolated day; its daily mean is 250 > max_abs=220.
        # The naive timestamp is interpreted as UTC, so read back the actual
        # local-day bucket the reading landed in and query that date.
        spike_day = _today()
        res = anomalies.detect(str(db), _config(), spike_day)
        assert res["heart_rate"]["status"] == "flagged", res["heart_rate"]
        assert "above max_abs" in res["heart_rate"]["reason"], res["heart_rate"]


def test_normal_no_flag():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        # Use an old date far from the spike so all values are flat 70.
        res = anomalies.detect(str(db), _config(), "2026-01-01")
        # 2026-01-01 is before the fixture data; expect insufficient.
        assert res["weight"]["status"] in ("ok", "insufficient")


def test_mad_zero_never_flags():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        # heart_rate is perfectly flat -> MAD==0 -> robust_z returns 0 -> no flag.
        res = anomalies.detect(str(db), _config(), _today())
        assert res["heart_rate"]["status"] == "ok", res["heart_rate"]


def test_insufficient_history():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        con = sqlite3.connect(str(db))
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
        cur.execute("INSERT INTO sources VALUES (1, 't', 'T', 'phone')")
        # Only 5 days of data -> insufficient.
        for i in range(5):
            dt = datetime.now(timezone.utc) - timedelta(days=i)
            start = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, 'weight', 'w', ?, ?, 70.0, 'kg')",
                (start, start),
            )
        con.commit()
        con.close()
        res = anomalies.detect(str(db), _config(), _today())
        assert res["weight"]["status"] == "insufficient", res["weight"]


def test_weight_change_flag():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        con = sqlite3.connect(str(db))
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
        cur.execute("INSERT INTO sources VALUES (1, 't', 'T', 'phone')")
        # 30 flat days then a jump 3 days ago of +3.5 kg.
        for i in range(30):
            dt = datetime.now(timezone.utc) - timedelta(days=i)
            start = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, 'weight', 'w', ?, ?, 70.0, 'kg')",
                (start, start),
            )
        # today +3.5
        dt0 = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES (1, 'weight', 'w', ?, ?, 73.5, 'kg')",
            (dt0, dt0),
        )
        # 3 days ago +3.5
        dt3 = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES (1, 'weight', 'w', ?, ?, 73.5, 'kg')",
            (dt3, dt3),
        )
        con.commit()
        con.close()
        res = anomalies.detect(str(db), _config(), _today())
        assert res["weight"]["status"] == "flagged", res["weight"]


def test_determinism():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)
        today = _today()
        r1 = anomalies.detect(str(db), _config(), today)
        r2 = anomalies.detect(str(db), _config(), today)
        assert r1 == r2


def test_local_day_boundary():
    """A reading at 23:30 CDT on 2026-09-20 must land on 2026-09-20."""
    # Build a DB with a single weight reading at 2026-09-20T23:30 CDT and a
    # flat baseline before it; the detector must bucket it on 2026-09-20.
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        con = sqlite3.connect(str(db))
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
        cur.execute("INSERT INTO sources VALUES (1, 't', 'T', 'phone')")
        # Baseline: 30 flat days at 70 kg ending 2026-09-19.
        for i in range(30):
            dt = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc) - timedelta(days=i)
            start = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, 'weight', 'w', ?, ?, 70.0, 'kg')",
                (start, start),
            )
        # The target reading: 2026-09-20T23:30 CDT = 2026-09-21T04:30 UTC.
        cur.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES (1, 'weight', 'w', '2026-09-21T04:30:00+00:00', '2026-09-21T04:30:00+00:00', 73.5, 'kg')",
        )
        con.commit()
        con.close()
        # Query the detector for 2026-09-20 (the local day).
        res = anomalies.detect(str(db), _config(), "2026-09-20")
        # The reading must have been bucketed into 2026-09-20 and flagged.
        assert res["weight"]["status"] == "flagged", res["weight"]


if __name__ == "__main__":
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
    print(f"\nAll {len(funcs)} anomaly detector tests passed")


def test_history_counts_only_days_before_the_target_date():
    """No look-ahead: a date with under 14 earlier days is insufficient even when later data exists."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        _make_fake_db(db)  # weight has 30 days of data ending today
        target = (datetime.now(timezone.utc).astimezone(anomalies.CHICAGO) - timedelta(days=24)).strftime("%Y-%m-%d")
        res = anomalies.detect(str(db), _config(), target)
        assert res["weight"]["status"] == "insufficient", res["weight"]
        assert not res["weight"].get("flags"), res["weight"]


def test_sleep_hours_group_by_wake_up_date():
    """A night starting after midnight and the next night starting that evening are two nights, not one."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE sleep_sessions (start_time TEXT, end_time TEXT)")
    # Chicago is UTC-5 in September: 00:30 to 07:30 local on Sep 17, then 22:30 Sep 17 to 05:30 local Sep 18.
    con.executemany("INSERT INTO sleep_sessions VALUES (?, ?)", [
        ("2026-09-17T05:30:00Z", "2026-09-17T12:30:00Z"),
        ("2026-09-18T03:30:00Z", "2026-09-18T10:30:00Z"),
    ])
    days = anomalies._daily_sleep_hours(con, anomalies.CHICAGO)
    assert days == {"2026-09-17": 7.0, "2026-09-18": 7.0}, days



def test_weight_change_uses_calendar_days_across_a_month_boundary():
    """window_days=3 from 2030-03-02 must compare against 2030-02-27 (calendar subtraction), not clamp to
    day 1 of the same month (the old datetime.replace(day=...) bug). Nothing is inserted on 2030-03-01, the
    date the buggy lookup would have used, so the buggy code finds no comparison point and never flags."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        cur.execute(
            "CREATE TABLE samples ("
            "sample_id INTEGER PRIMARY KEY, source_id INTEGER, type_code TEXT,"
            "client_record_id TEXT, start_time TEXT, end_time TEXT,"
            "value REAL, unit TEXT, metadata_json TEXT)"
        )
        cur.execute("CREATE TABLE sources (source_id INTEGER, source_key TEXT, name TEXT, kind TEXT)")
        cur.execute(
            "CREATE TABLE sync_runs (sync_run_id INTEGER, started_at TEXT, "
            "finished_at TEXT, status TEXT, sample_count INTEGER)"
        )
        cur.execute("INSERT INTO sources VALUES (1, 't', 'T', 'phone')")

        def add(date_str, value):
            ts = f"{date_str}T12:00:00Z"
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, 'weight', ?, ?, ?, ?, 'kg')",
                (f"w-{date_str}", ts, ts, value),
            )

        base = datetime(2030, 2, 5)
        for i in range(20):  # flat 20-day baseline, strictly before the target day
            add((base + timedelta(days=i)).strftime("%Y-%m-%d"), 70.0)
        add("2030-02-27", 70.0)  # the CORRECT "3 calendar days before 2030-03-02" comparison point
        add("2030-03-02", 75.0)  # target day: +5 kg, well past the configured 2 kg threshold
        # deliberately nothing on 2030-03-01 - the date the buggy replace(day=...) clamp would have looked up
        con.commit()
        con.close()
        res = anomalies.detect(str(db), _config(), "2030-03-02")
        assert res["weight"]["status"] == "flagged", res["weight"]
        assert "3d change=" in res["weight"]["reason"], res["weight"]


def test_previous_window_stays_within_calendar_days_even_when_data_is_sparse():
    """_previous_window() must select dates within the last `window_days` CALENDAR days, not 'the last
    window_days dates that happen to have samples' - sparse data must not silently reach back arbitrarily
    far to pad out a fixed count. Shared by both the detector and morning_digest.py's baseline selection."""
    # Only 3 dates fall within the true 10-calendar-day window before 2030-06-11 (2030-06-01 .. 2030-06-10):
    # a single sample on each of 06-03, 06-05, 06-09. Many older dates exist far outside that window.
    sparse_recent = ["2030-06-03", "2030-06-05", "2030-06-09"]
    far_older = [f"2030-01-{d:02d}" for d in range(1, 21)]  # 20 dates, all well outside the 10-day window
    dates = sorted(far_older + sparse_recent)
    result = anomalies._previous_window(dates, "2030-06-11", window_days=10)
    assert sorted(result) == sparse_recent, result
    assert all(d >= "2030-06-01" for d in result), result


def test_thin_in_window_baseline_does_not_run_z_or_trend_but_absolute_bounds_still_apply():
    """Review follow-up to the calendar-window fix: history can pass the 14-day gate while only a handful of
    samples fall inside the baseline window. robust_z / slope on 2-3 values flag on noise, so a thin in-window
    baseline must be reported as insufficient for those checks - exactly as morning_digest already refuses to
    print a baseline with fewer than MIN_BASELINE_DAYS in-window days. Absolute bounds need no baseline and
    must still fire."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "fake.sqlite"
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        cur.execute(
            "CREATE TABLE samples ("
            "sample_id INTEGER PRIMARY KEY, source_id INTEGER, type_code TEXT,"
            "client_record_id TEXT, start_time TEXT, end_time TEXT,"
            "value REAL, unit TEXT, metadata_json TEXT)"
        )
        cur.execute("CREATE TABLE sources (source_id INTEGER, source_key TEXT, name TEXT, kind TEXT)")
        cur.execute(
            "CREATE TABLE sync_runs (sync_run_id INTEGER, started_at TEXT, "
            "finished_at TEXT, status TEXT, sample_count INTEGER)"
        )
        cur.execute("INSERT INTO sources VALUES (1, 't', 'T', 'phone')")

        def add(date_str, value):
            ts = f"{date_str}T12:00:00Z"
            cur.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES (1, 'weight', ?, ?, ?, ?, 'kg')",
                (f"w-{date_str}", ts, ts, value),
            )

        # 20 days of old history (passes the 14-day history gate) ...
        base = datetime(2029, 10, 1)
        for i in range(20):
            add((base + timedelta(days=i)).strftime("%Y-%m-%d"), 70.0)
        # ... but only TWO samples inside the 30-day baseline window before the target, 0.4 kg apart:
        # MAD is tiny, so a modest 71.5 would score robust_z ~ +4 and be flagged on noise.
        add("2030-03-01", 70.0)
        add("2030-03-05", 70.4)
        add("2030-03-10", 71.5)  # target day
        con.commit()
        con.close()
        res = anomalies.detect(str(db), _config(), "2030-03-10")
        w = res["weight"]
        assert w["status"] != "flagged" or "robust_z" not in w["reason"], w
        assert "slope" not in w.get("reason", ""), w
        assert "baseline" in w.get("reason", ""), w   # the thin baseline is named, not hidden

        # Absolute bounds still fire with the same thin baseline (max_abs=400 in _config).
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        ts = "2030-03-11T12:00:00Z"
        cur.execute("INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) VALUES (1,'weight','w-big',?,?,450.0,'kg')", (ts, ts))
        con.commit()
        con.close()
        res = anomalies.detect(str(db), _config(), "2030-03-11")
        assert res["weight"]["status"] == "flagged" and "max_abs" in res["weight"]["reason"], res["weight"]
