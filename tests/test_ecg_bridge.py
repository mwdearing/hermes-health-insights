"""Tests for ECG bridge import (read_ecgs_bridge, dedupe, CLI)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import pytest

from health_insights.ecg import (
    CHI,
    EcgRecording,
    open_db,
    read_ecgs_bridge,
    cmd_import_bridge,
)


def _make_bridge_db(records: list[dict], path: str) -> None:
    """Create a bridge DB with the electrocardiograms table."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE electrocardiograms (
            client_record_id TEXT,
            start_time TEXT,
            end_time TEXT,
            classification TEXT,
            symptoms_status TEXT,
            average_heart_rate_bpm TEXT,
            sampling_frequency_hz TEXT,
            voltage_count INTEGER,
            voltages_json TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO electrocardiograms VALUES (?,?,?,?,?,?,?,?,?)",
        records,
    )
    conn.commit()
    conn.close()


# --- Summer offset (CDT, UTC-5) ---

def test_bridge_summer_offset(tmp_path):
    """A 2031-06-04T13:05:00Z start_time should become 2031-06-04T08:05:00-05:00."""
    db = str(tmp_path / "bridge.db")
    _make_bridge_db(
        [
            (
                "client-1",
                "2031-06-04T13:05:00Z",
                "2031-06-04T13:05:30Z",
                "sinus_rhythm",
                "none",
                "72",
                "500",
                15000,
                "[]",
            )
        ],
        db,
    )
    recordings, rejected = read_ecgs_bridge(db)
    assert len(rejected) == 0
    assert len(recordings) == 1
    rec = recordings[0]
    assert rec.recorded_at == "2031-06-04T08:05:00-05:00"
    assert rec.recorded_local_date == "2031-06-04"
    assert rec.classification == "Sinus Rhythm"
    assert rec.symptoms == "None"
    assert rec.duration_s == 30.0
    assert rec.sample_count == 15000
    assert rec.sample_rate == 500
    assert rec.device == "HealthRelay bridge"
    assert rec.source_file == "bridge:client-1"


# --- Winter offset (CST, UTC-6) ---

def test_bridge_winter_offset(tmp_path):
    """A 2031-01-15T13:05:00Z start_time should become 2031-01-15T07:05:00-06:00."""
    db = str(tmp_path / "bridge.db")
    _make_bridge_db(
        [
            (
                "client-2",
                "2031-01-15T13:05:00Z",
                "2031-01-15T13:05:30Z",
                "atrial_fibrillation",
                "present",
                "80",
                "500",
                15000,
                "[]",
            )
        ],
        db,
    )
    recordings, rejected = read_ecgs_bridge(db)
    assert len(rejected) == 0
    assert len(recordings) == 1
    rec = recordings[0]
    assert rec.recorded_local_date == "2031-01-15"
    assert rec.classification == "Atrial Fibrillation"
    assert rec.symptoms == "Present"


# --- Classification mapping ---

@pytest.mark.parametrize(
    "bridge_class,expected",
    [
        ("sinus_rhythm", "Sinus Rhythm"),
        ("atrial_fibrillation", "Atrial Fibrillation"),
        ("inconclusive_low_heart_rate", "Inconclusive (Low Heart Rate)"),
        ("inconclusive_high_heart_rate", "Inconclusive (High Heart Rate)"),
        ("inconclusive_poor_reading", "Inconclusive (Poor Reading)"),
        ("inconclusive_other", "Inconclusive"),
        ("unrecognized", "Unrecognized"),
        ("not_set", "Not Set"),
    ],
)
def test_classification_mapping(tmp_path, bridge_class, expected):
    db = str(tmp_path / "bridge.db")
    _make_bridge_db(
        [
            (
                "c",
                "2031-06-04T13:05:00Z",
                "2031-06-04T13:05:30Z",
                bridge_class,
                "none",
                "72",
                "500",
                15000,
                "[]",
            )
        ],
        db,
    )
    recordings, _ = read_ecgs_bridge(db)
    assert recordings[0].classification == expected


# --- Symptoms mapping ---

@pytest.mark.parametrize(
    "bridge_sym,expected",
    [
        ("none", "None"),
        ("present", "Present"),
        ("not_set", "Not Set"),
    ],
)
def test_symptoms_mapping(tmp_path, bridge_sym, expected):
    db = str(tmp_path / "bridge.db")
    _make_bridge_db(
        [
            (
                "c",
                "2031-06-04T13:05:00Z",
                "2031-06-04T13:05:30Z",
                "sinus_rhythm",
                bridge_sym,
                "72",
                "500",
                15000,
                "[]",
            )
        ],
        db,
    )
    recordings, _ = read_ecgs_bridge(db)
    assert recordings[0].symptoms == expected


# --- Dedupe against a CSV-style row at the same instant ---

def test_dedupe_same_instant(tmp_path):
    """A recording whose recorded_at instant already exists in ecg_recordings should be skipped."""
    bridge_db = str(tmp_path / "bridge.db")
    ecg_db = str(tmp_path / "ecg.sqlite")

    # Pre-populate ecg_recordings with a row at the same UTC instant but different member
    conn = open_db(ecg_db)
    conn.execute(
        "INSERT INTO ecg_recordings "
        "(member, recorded_at, recorded_local_date, classification, symptoms, device, "
        "software_version, sample_rate, sample_count, duration_s, lead, unit, imported_at, source_file) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "csv-file.csv",
            "2031-06-04T13:05:00+00:00",
            "2031-06-04",
            "Sinus Rhythm",
            "None",
            "Apple Watch",
            "",
            500,
            15000,
            30.0,
            "Lead I",
            "µV",
            "2031-06-04T12:00:00+00:00",
            "csv:csv-file.csv",
        ),
    )
    conn.commit()
    conn.close()

    # Bridge has the same instant, different client_record_id
    _make_bridge_db(
        [
            (
                "client-x",
                "2031-06-04T13:05:00Z",
                "2031-06-04T13:05:30Z",
                "sinus_rhythm",
                "none",
                "72",
                "500",
                15000,
                "[]",
            )
        ],
        bridge_db,
    )

    # Import via CLI function
    rc = cmd_import_bridge(bridge_db, ecg_db)
    assert rc == 0

    # Verify: still only 1 row (the bridge row was skipped as duplicate)
    conn = sqlite3.connect(ecg_db)
    total = conn.execute("SELECT COUNT(*) FROM ecg_recordings").fetchone()[0]
    conn.close()
    assert total == 1


# --- Idempotent second import ---

def test_idempotent_import(tmp_path):
    """Importing the same bridge DB twice should not add duplicates."""
    bridge_db = str(tmp_path / "bridge.db")
    ecg_db = str(tmp_path / "ecg.sqlite")
    _make_bridge_db(
        [
            (
                "client-1",
                "2031-06-04T13:05:00Z",
                "2031-06-04T13:05:30Z",
                "sinus_rhythm",
                "none",
                "72",
                "500",
                15000,
                "[]",
            )
        ],
        bridge_db,
    )

    cmd_import_bridge(bridge_db, ecg_db)
    cmd_import_bridge(bridge_db, ecg_db)

    conn = sqlite3.connect(ecg_db)
    total = conn.execute("SELECT COUNT(*) FROM ecg_recordings").fetchone()[0]
    conn.close()
    assert total == 1


# --- Missing table ---

def test_missing_table(tmp_path):
    """A bridge DB without the electrocardiograms table should return empty."""
    db = str(tmp_path / "bridge.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other_table (id INTEGER)")
    conn.commit()
    conn.close()
    recordings, rejected = read_ecgs_bridge(db)
    assert recordings == []
    assert rejected == []


# --- CLI dispatch ---

def test_cli_dispatch(tmp_path):
    """python -m health_insights.cli ecg import-bridge should work."""
    bridge_db = str(tmp_path / "bridge.db")
    ecg_db = str(tmp_path / "ecg.sqlite")
    _make_bridge_db(
        [
            (
                "client-1",
                "2031-06-04T13:05:00Z",
                "2031-06-04T13:05:30Z",
                "sinus_rhythm",
                "none",
                "72",
                "500",
                15000,
                "[]",
            )
        ],
        bridge_db,
    )
    result = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "ecg", "import-bridge",
         "--bridge", bridge_db, "--db", ecg_db],
        cwd=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "ECG import: 1 new, 0 already present, 0 rejected" in result.stdout
