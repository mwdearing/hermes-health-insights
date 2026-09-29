"""Deterministic synthetic bridge-style database for trying the CLI without any real data."""
from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from . import settings

_SCHEMA = (
    "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, "
    "client_record_id TEXT, start_time TEXT, end_time TEXT, value REAL, unit TEXT, metadata_json TEXT)",
    "CREATE TABLE sources (source_id TEXT PRIMARY KEY, source_key TEXT)",
    "CREATE TABLE sync_runs (sync_run_id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, sample_count INTEGER)",
    "CREATE TABLE sleep_sessions (sleep_session_id INTEGER PRIMARY KEY, source_id INTEGER, "
    "client_record_id TEXT, start_time TEXT, end_time TEXT)",
)


def build(path: str, days: int = 60, end: date | None = None, seed: int = 7) -> str:
    """Write about `days` days of plausible-looking synthetic data ending on `end` (default: today)."""
    rng = random.Random(seed)
    tz = settings.timezone()
    end = end or (datetime.now(tz).date() - timedelta(days=1))
    con = sqlite3.connect(path)
    for stmt in _SCHEMA:
        con.execute(stmt)
    con.execute("INSERT INTO sources VALUES ('demo', 'demo.phone')")
    con.execute("INSERT INTO sources VALUES ('1', 'demo.watch')")
    n = [0]

    def add(code: str, day: date, hour: int, value: float, unit: str, minute: int = 0) -> None:
        n[0] += 1
        local = datetime.combine(day, time(hour, minute), tzinfo=tz)
        ts = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES ('demo',?,?,?,?,?,?)", (code, f"demo-{n[0]}", ts, ts, value, unit))

    add("height", end - timedelta(days=days), 8, 178.0, "cm")
    weight = 82.0
    for i in range(days):
        day = end - timedelta(days=days - 1 - i)
        weight += rng.uniform(-0.15, 0.13)
        add("weight", day, 7, round(weight, 1), "kg")
        add("resting_heart_rate", day, 8, round(rng.gauss(60, 2.5)), "bpm")
        add("heart_rate_variability_sdnn", day, 8, round(rng.gauss(52, 6), 1), "ms")
        add("oxygen_saturation", day, 3, round(min(0.995, max(0.93, rng.gauss(0.965, 0.008))), 3), "fraction")
        add("blood_pressure_systolic", day, 9, round(rng.gauss(121, 6)), "mmHg", 5)
        add("blood_pressure_diastolic", day, 9, round(rng.gauss(78, 4)), "mmHg", 5)
        if rng.random() < 0.85:  # most days have a food log
            add("dietary_energy_consumed", day, 12, round(rng.gauss(2350, 250)), "kcal")
            add("dietary_protein", day, 12, round(rng.gauss(115, 15)), "g")
            add("dietary_fiber", day, 12, round(rng.gauss(24, 5)), "g")
            add("dietary_sodium", day, 12, round(rng.gauss(2900, 500)), "mg")
        bed = datetime.combine(day - timedelta(days=1), time(22, 45), tzinfo=tz) + timedelta(minutes=rng.randint(-30, 45))
        wake = bed + timedelta(hours=7, minutes=rng.randint(-40, 50))
        n[0] += 1
        con.execute(
            "INSERT INTO sleep_sessions (source_id, client_record_id, start_time, end_time) VALUES (1,?,?,?)",
            (f"demo-sleep-{n[0]}", bed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             wake.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        started = datetime.combine(day, time(21, 0), tzinfo=tz).astimezone(timezone.utc)
        con.execute(
            "INSERT INTO sync_runs (started_at, finished_at, status, sample_count) VALUES (?,?,?,?)",
            (started.strftime("%Y-%m-%dT%H:%M:%SZ"), (started + timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "succeeded", 10))
    con.commit()
    con.close()
    return path
