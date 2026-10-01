"""health-insights doctor: say exactly where the data comes from and whether it is there, before anyone reports on it.

Prints where the configuration came from, the resolved receiver (bridge) and optional history database paths with
exists / readable / opened-read-only checks, and for each metric the skills use the newest sample date and the number
of distinct days with data in the last 30. A wrong or empty path is an ERROR here, never a silent "no data".

Exit codes: 0 OK, 1 warnings (stale or sparse data), 2 error (database not configured, missing, empty or unreadable).
Counts and dates only: no values are printed.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from health_insights import settings
from health_insights.sqlite_ro import connect_readonly

# Metrics the shipped skills rely on (type_code in the samples table).
METRICS = ("resting_heart_rate", "heart_rate_variability_sdnn", "weight", "steps", "blood_pressure_systolic", "hydration")
SLEEP = "sleep_sessions"  # a table of its own; counted by the local date each session ends on
WINDOW_DAYS = 30


def _source_of_bridge_db() -> tuple[str | None, str]:
    env = os.environ.get("HEALTH_INSIGHTS_BRIDGE_DB")
    if env:
        return env, "HEALTH_INSIGHTS_BRIDGE_DB"
    cfg = settings._config().get("bridge_db")
    if cfg:
        return str(cfg), f"bridge_db in {settings.config_path()}"
    from_file = settings._db_path_from_file()
    if from_file:
        return from_file, str(settings.healthrelay_db_path_file())
    return None, "not set"


def _history_db() -> str:
    return os.environ.get("HEALTH_INSIGHTS_DB") or str(settings.data_dir() / "history" / "merged_history.sqlite")


def _local_day(stamp: str):
    when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(settings.timezone()).date()


def check_database(path: str) -> dict:
    """exists / non-empty / opens read-only / the write probe is refused / has the bridge tables."""
    info: dict = {"path": path, "exists": os.path.isfile(path)}
    if not info["exists"]:
        info["problem"] = "not found"
        return info
    info["bytes"] = os.path.getsize(path)
    if info["bytes"] == 0:
        info["problem"] = "0-byte file, not a database"
        return info
    try:
        conn = connect_readonly(path)
    except (sqlite3.Error, OSError) as exc:
        info["readable"] = False
        info["problem"] = f"cannot open read-only ({exc.__class__.__name__})"
        return info
    try:
        try:
            tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        except sqlite3.Error as exc:
            info["readable"] = False
            info["problem"] = f"unreadable ({exc.__class__.__name__})"
            return info
        info["readable"] = True
        try:
            conn.execute("create table _health_insights_doctor_probe (x)")
            info["opened_read_only"] = False
            info["problem"] = "the connection is NOT read-only"
        except sqlite3.OperationalError:
            info["opened_read_only"] = True
        info["has_samples_table"] = "samples" in tables
        info["has_sleep_sessions_table"] = SLEEP in tables
        if not info["has_samples_table"] and "problem" not in info:
            info["problem"] = "no samples table (not a bridge database)"
    finally:
        conn.close()
    return info


def _metric_rows(path: str, today) -> dict:
    cutoff = (datetime.combine(today - timedelta(days=WINDOW_DAYS + 1), datetime.min.time())).strftime("%Y-%m-%d")
    conn = connect_readonly(path)
    out: dict = {}
    try:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        for code in METRICS:
            newest = conn.execute("select max(start_time) from samples where type_code=?", (code,)).fetchone()[0]
            days = {_local_day(st) for (st,) in conn.execute(
                "select start_time from samples where type_code=? and start_time >= ?", (code, cutoff))}
            days = {d for d in days if 0 <= (today - d).days < WINDOW_DAYS}
            out[code] = {"last_sample_date": _local_day(newest).isoformat() if newest else None, "days_with_data_30": len(days)}
        if SLEEP in tables:
            newest = conn.execute(f"select max(end_time) from {SLEEP}").fetchone()[0]
            days = {_local_day(et) for (et,) in conn.execute(f"select end_time from {SLEEP} where end_time >= ?", (cutoff,))}
            days = {d for d in days if 0 <= (today - d).days < WINDOW_DAYS}
            out["sleep"] = {"last_sample_date": _local_day(newest).isoformat() if newest else None, "days_with_data_30": len(days)}
        newest_any = conn.execute("select max(start_time) from samples").fetchone()[0]
    finally:
        conn.close()
    return {"metrics": out, "newest_any": _local_day(newest_any).isoformat() if newest_any else None}


def build_report(db: str | None = None, stale_days: int | None = None, today=None) -> dict:
    stale = stale_days if stale_days is not None else settings.stale_days()
    today = today or datetime.now(settings.timezone()).date()
    cfg_path = settings.config_path()
    report: dict = {
        "config_file": {"path": str(cfg_path), "exists": cfg_path.is_file()},
        "timezone": settings.timezone_name(),
        "stale_days": stale,
        "errors": [],
        "warnings": [],
    }
    if db:
        path, source = db, "--db"
    else:
        path, source = _source_of_bridge_db()
    report["bridge_db"] = {"source": source}
    if not path:
        report["errors"].append(
            "bridge_db is not set: use --db, HEALTH_INSIGHTS_BRIDGE_DB, bridge_db in the config file, or ~/.config/healthrelay/db-path")
    else:
        info = check_database(path)
        report["bridge_db"].update(info)
        if info.get("problem"):
            report["errors"].append(f"{path}: {info['problem']}")
        else:
            data = _metric_rows(path, today)
            report["metrics"] = data["metrics"]
            newest = data["newest_any"]
            report["newest_sample_date"] = newest
            if newest is None:
                report["errors"].append(f"{path}: the database has no samples at all")
            elif (today - datetime.fromisoformat(newest).date()).days > stale:
                report["warnings"].append(f"newest data is from {newest}, older than {stale} days (phone sync stopped?)")
            for name, m in data["metrics"].items():
                if m["last_sample_date"] and m["days_with_data_30"] == 0:
                    report["warnings"].append(f"{name}: sparse, no data in the last {WINDOW_DAYS} days (last {m['last_sample_date']})")
                elif not m["last_sample_date"]:
                    report["warnings"].append(f"{name}: never recorded in this database")
    history = _history_db()
    report["history_db"] = {"path": history, "exists": os.path.isfile(history), "optional": True}
    return report


def run(db: str | None = None, stale_days: int | None = None, as_json: bool = False) -> int:
    report = build_report(db, stale_days)
    code = 2 if report["errors"] else 1 if report["warnings"] else 0
    report["status"] = {0: "ok", 1: "warning", 2: "error"}[code]
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    cfg = report["config_file"]
    print(f"config file: {cfg['path']} ({'found' if cfg['exists'] else 'not found, using defaults'}); time zone {report['timezone']}")
    bridge = report["bridge_db"]
    print(f"bridge db: {bridge.get('path', '(not set)')} [from {bridge['source']}]")
    for key in ("exists", "bytes", "readable", "opened_read_only", "has_samples_table", "has_sleep_sessions_table"):
        if key in bridge:
            print(f"  {key}: {bridge[key]}")
    hist = report["history_db"]
    print(f"history db: {hist['path']} ({'found' if hist['exists'] else 'not present; optional'})")
    for name, m in report.get("metrics", {}).items():
        print(f"  {name}: last sample {m['last_sample_date'] or 'never'}, {m['days_with_data_30']} of {WINDOW_DAYS} days with data")
    if report.get("newest_sample_date"):
        print(f"newest sample: {report['newest_sample_date']} (stale after {report['stale_days']} days)")
    for err in report["errors"]:
        print(f"ERROR: {err}")
    for warn in report["warnings"]:
        print(f"WARNING: {warn}")
    print(f"status: {report['status']}")
    return code
