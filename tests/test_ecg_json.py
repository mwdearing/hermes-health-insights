"""Tests for ECG JSON import (B-4).

Synthetic data only. No real paths, no external data dirs.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(data, tmpdir: Path) -> Path:
    """Write *data* as JSON to a temp file and return its path."""
    p = tmpdir / "ecg.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _make_recording(
    rid: str = "rec-1",
    recorded_at: str = "2026-09-26T03:30:00-05:00",
    classification: str = "Sinus Rhythm",
    symptoms: str = "None",
    device: str = "Apple Watch Series 9",
    software_version: str = "9.4.1",
    sampling_frequency_hz: int = 512,
    lead: str = "Single-Lead",
    unit: str = "mV",
    voltages: list[float] | None = None,
) -> dict:
    """Return a minimal synthetic recording dict."""
    if voltages is None:
        voltages = [0.001, 0.002, 0.003]  # tiny, never stored
    return {
        "id": rid,
        "recorded_at": recorded_at,
        "classification": classification,
        "symptoms": symptoms,
        "device": device,
        "software_version": software_version,
        "sampling_frequency_hz": sampling_frequency_hz,
        "lead": lead,
        "unit": unit,
        "voltages": voltages,
    }

# ---------------------------------------------------------------------------
# 1. Wrapped payload (object with "recordings" key)
# ---------------------------------------------------------------------------

def test_wrapped_payload(tmp_path):
    from health_insights.ecg import read_ecgs_json

    payload = {"schema": "hde.ecg.v1", "recordings": [_make_recording()]}
    p = _write_json(payload, tmp_path)
    recs, rej = read_ecgs_json(p)
    assert len(rej) == 0
    assert len(recs) == 1
    assert recs[0].member == "rec-1"
    assert recs[0].sample_count == 3
    assert recs[0].sample_rate == 512
    assert recs[0].duration_s == pytest.approx(3 / 512, abs=0.001)

# ---------------------------------------------------------------------------
# 2. Bare list of recordings
# ---------------------------------------------------------------------------

def test_bare_list(tmp_path):
    from health_insights.ecg import read_ecgs_json

    recs, rej = read_ecgs_json([_make_recording()])
    assert len(recs) == 1
    assert len(rej) == 0

# ---------------------------------------------------------------------------
# 3. Field mapping — all fields correctly mapped
# ---------------------------------------------------------------------------

def test_field_mapping(tmp_path):
    from health_insights.ecg import read_ecgs_json

    rec = _make_recording(
        rid="rec-42",
        recorded_at="2026-09-26T03:30:00-05:00",
        classification="Atrial Fibrillation",
        symptoms="Palpitations",
        device="Apple Watch Ultra 2",
        software_version="10.1.0",
        sampling_frequency_hz=256,
        lead="Lead I",
        unit="mV",
    )
    recs, _ = read_ecgs_json([rec])
    r = recs[0]
    assert r.member == "rec-42"
    assert r.recorded_at == "2026-09-26T08:30:00+00:00"
    assert r.recorded_local_date == "2026-09-26"
    assert r.classification == "Atrial Fibrillation"
    assert r.symptoms == "Palpitations"
    assert r.device == "Apple Watch Ultra 2"
    assert r.software_version == "10.1.0"
    assert r.sample_rate == 256
    assert r.sample_count == 3
    assert r.lead == "Lead I"
    assert r.unit == "mV"

# ---------------------------------------------------------------------------
# 4. Chicago date across midnight
# ---------------------------------------------------------------------------

def test_chicago_midnight(tmp_path):
    from health_insights.ecg import read_ecgs_json

    # 11:30 PM EDT (UTC-4) = 03:30 CDT (UTC-5) next day
    rec = _make_recording(
        rid="rec-mid",
        recorded_at="2026-09-26T23:30:00-04:00",
    )
    recs, _ = read_ecgs_json([rec])
    r = recs[0]
    # 23:30-04:00 = 03:30 UTC → Chicago date is 2026-09-26
    assert r.recorded_at == "2026-09-27T03:30:00+00:00"
    assert r.recorded_local_date == "2026-09-26"

# ---------------------------------------------------------------------------
# 5. Rejected recordings — missing id, missing recorded_at, unparsable date
# ---------------------------------------------------------------------------

def test_rejects_missing_id(tmp_path):
    from health_insights.ecg import read_ecgs_json

    bad = _make_recording()
    bad["id"] = None  # missing id → reject
    recs, rej = read_ecgs_json([bad])
    assert len(recs) == 0
    assert len(rej) == 1


def test_rejects_missing_recorded_at(tmp_path):
    from health_insights.ecg import read_ecgs_json

    bad = _make_recording()
    del bad["recorded_at"]  # missing recorded_at → reject
    recs, rej = read_ecgs_json([bad])
    assert len(recs) == 0
    assert len(rej) == 1


def test_rejects_unparsable_date(tmp_path):
    from health_insights.ecg import read_ecgs_json

    bad = _make_recording()
    bad["recorded_at"] = "not-a-date"
    recs, rej = read_ecgs_json([bad])
    assert len(recs) == 0
    assert len(rej) == 1

# ---------------------------------------------------------------------------
# 6. Empty list → (empty, empty)
# ---------------------------------------------------------------------------

def test_empty_list(tmp_path):
    from health_insights.ecg import read_ecgs_json

    recs, rej = read_ecgs_json([])
    assert recs == []
    assert rej == []

# ---------------------------------------------------------------------------
# 7. Bad top-level types raise ValueError
# ---------------------------------------------------------------------------

def test_bad_top_level_string(tmp_path):
    from health_insights.ecg import read_ecgs_json

    with pytest.raises(ValueError, match="recordings"):
        read_ecgs_json("not a list or object")


def test_bad_top_level_int(tmp_path):
    from health_insights.ecg import read_ecgs_json

    with pytest.raises(ValueError, match="recordings"):
        read_ecgs_json(42)

# ---------------------------------------------------------------------------
# 8. Idempotent CLI — import-json twice, same counts
# ---------------------------------------------------------------------------

def test_idempotent_cli(tmp_path):
    db = str(tmp_path / "ecg.sqlite")
    rec = _make_recording()
    p = _write_json([rec], tmp_path)

    # First import
    r1 = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "ecg", "import-json", str(p), "--db", db],
        capture_output=True, text=True, timeout=30,
    )
    assert r1.returncode == 0
    assert "1 new" in r1.stdout
    assert "0 already present" in r1.stdout

    # Second import — idempotent
    r2 = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "ecg", "import-json", str(p), "--db", db],
        capture_output=True, text=True, timeout=30,
    )
    assert r2.returncode == 0
    assert "0 new" in r2.stdout
    assert "1 already present" in r2.stdout

# ---------------------------------------------------------------------------
# 9. No voltage stored — privacy rule
# ---------------------------------------------------------------------------

def test_no_voltage_stored(tmp_path):
    from health_insights.ecg import read_ecgs_json, ensure_db_dir, open_db, insert_recordings

    rec = _make_recording()
    recs, _ = read_ecgs_json([rec])
    db = str(tmp_path / "ecg.sqlite")
    ensure_db_dir(os.path.dirname(db))
    conn = open_db(db)
    insert_recordings(conn, recs)

    # Read back — voltages column should not exist in ecg_recordings
    cols = [row[1] for row in conn.execute("PRAGMA table_info(ecg_recordings)").fetchall()]
    assert "voltages" not in cols
    assert "samples" not in cols
    assert "voltage" not in cols
    conn.close()
