"""Tests for lab-results bridge import (read_labs_bridge, dedupe, CLI)."""
from __future__ import annotations

import sqlite3
import subprocess
import sys

from health_insights.labs import (
    LabResult,
    cmd_import_bridge,
    insert_results,
    open_db,
    read_labs_bridge,
)


def _make_bridge_db(records: list[tuple], path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE lab_results (
            client_record_id TEXT,
            loinc TEXT,
            name TEXT,
            category TEXT,
            effective_date TEXT,
            value_num REAL,
            unit TEXT,
            value_text TEXT,
            ref_low REAL,
            ref_high REAL,
            ref_text TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO lab_results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        records,
    )
    conn.commit()
    conn.close()


ROW_SODIUM = (
    "hk-labobs-aaaaaaaaaaaaaaaa", "2951-2", "Sodium", "laboratory",
    "2026-06-04", 140.0, "mmol/L", None, 136.0, 145.0, None,
)
ROW_GLUCOSE = (
    "hk-labobs-bbbbbbbbbbbbbbbb", "2345-7", "Glucose", "laboratory",
    "2026-06-05", 95.0, "mg/dL", None, 70.0, 100.0, None,
)
ROW_TEXT_ONLY = (
    "hk-labobs-cccccccccccccccc", None, "Culture Result", "laboratory",
    "2026-06-06", None, None, "Negative", None, None, None,
)


def test_read_labs_bridge_basic(tmp_path):
    db = str(tmp_path / "bridge.db")
    _make_bridge_db([ROW_SODIUM], db)

    results, rejected = read_labs_bridge(db)

    assert rejected == []
    assert len(results) == 1
    r = results[0]
    assert r.loinc == "2951-2"
    assert r.name == "Sodium"
    assert r.effective_date == "2026-06-04"
    assert r.value_num == 140.0
    assert r.unit == "mmol/L"
    assert r.ref_low == 136.0
    assert r.ref_high == 145.0
    assert r.flag == "NORMAL"
    assert r.obs_id == "bridge:hk-labobs-aaaaaaaaaaaaaaaa"
    assert r.source_file == "bridge:hk-labobs-aaaaaaaaaaaaaaaa"


def test_missing_table_returns_empty(tmp_path):
    db = str(tmp_path / "bridge.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sources (source_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    results, rejected = read_labs_bridge(db)

    assert results == []
    assert rejected == []


def test_row_missing_effective_date_is_rejected(tmp_path):
    db = str(tmp_path / "bridge.db")
    bad_row = list(ROW_SODIUM)
    bad_row[4] = None  # effective_date
    _make_bridge_db([tuple(bad_row)], db)

    results, rejected = read_labs_bridge(db)

    assert results == []
    assert rejected == ["hk-labobs-aaaaaaaaaaaaaaaa"]


def test_text_only_value_is_read(tmp_path):
    db = str(tmp_path / "bridge.db")
    _make_bridge_db([ROW_TEXT_ONLY], db)

    results, _ = read_labs_bridge(db)

    assert results[0].value_text == "Negative"
    assert results[0].value_num is None
    assert results[0].flag == "UNKNOWN"


def test_dedupe_against_json_imported_row_with_different_obs_id(tmp_path):
    """The same real-world result may already be in labs.sqlite from an
    export.zip JSON import under the raw FHIR id, not the bridge's hashed
    client_record_id -- dedupe must match by content, not by obs_id."""
    bridge_db = str(tmp_path / "bridge.db")
    labs_db = str(tmp_path / "labs.sqlite")
    _make_bridge_db([ROW_SODIUM], bridge_db)

    conn = open_db(labs_db)
    insert_results(
        conn,
        [
            LabResult(
                obs_id="0258BDB7-6AB2-4336-96BE-CDB567975315",
                loinc="2951-2",
                name="Sodium",
                category="laboratory",
                effective_date="2026-06-04",
                value_num=140.0,
                unit="mmol/L",
                value_text=None,
                ref_low=136.0,
                ref_high=145.0,
                ref_text=None,
                flag="NORMAL",
                imported_at="2026-06-05T00:00:00+00:00",
                source_file="apple_health_export/clinical-records/Observation-0258.json",
            ),
        ],
    )
    conn.close()

    cmd_import_bridge(bridge_db, labs_db)

    with sqlite3.connect(labs_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0]
    assert count == 1  # not duplicated


def test_import_bridge_inserts_new_and_counts(tmp_path, capsys):
    bridge_db = str(tmp_path / "bridge.db")
    labs_db = str(tmp_path / "labs.sqlite")
    _make_bridge_db([ROW_SODIUM, ROW_GLUCOSE], bridge_db)

    cmd_import_bridge(bridge_db, labs_db)

    out = capsys.readouterr().out
    assert "Labs import: 2 new, 0 already present, 0 rejected" in out
    with sqlite3.connect(labs_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0]
    assert count == 2


def test_import_bridge_is_idempotent(tmp_path, capsys):
    bridge_db = str(tmp_path / "bridge.db")
    labs_db = str(tmp_path / "labs.sqlite")
    _make_bridge_db([ROW_SODIUM], bridge_db)

    cmd_import_bridge(bridge_db, labs_db)
    _ = capsys.readouterr()
    cmd_import_bridge(bridge_db, labs_db)

    out = capsys.readouterr().out
    assert "Labs import: 0 new, 1 already present, 0 rejected" in out


def test_cli_dispatch(tmp_path):
    bridge_db = str(tmp_path / "bridge.db")
    labs_db = str(tmp_path / "labs.sqlite")
    _make_bridge_db([ROW_SODIUM], bridge_db)

    result = subprocess.run(
        [sys.executable, "-m", "health_insights.cli", "labs", "import-bridge",
         "--bridge", bridge_db, "--db", labs_db],
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0
    assert "Labs import: 1 new, 0 already present, 0 rejected" in result.stdout
