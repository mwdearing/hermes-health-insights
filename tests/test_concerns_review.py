"""Review fixes for the concerns engine (2026-09-26): synthetic data only."""
import os
import sqlite3
import tempfile
from datetime import date

from health_insights import concerns

SCR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".scratch-w3-tests")


def _bp_db(name, readings):
    os.makedirs(SCR, exist_ok=True)
    path = os.path.join(SCR, name)
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, client_record_id TEXT, "
                "start_time TEXT, end_time TEXT, value REAL, unit TEXT, metadata_json TEXT, UNIQUE (type_code, start_time, value))")
    for i, (t, s, d) in enumerate(readings):
        for code, v, tag in (("blood_pressure_systolic", s, "s"), ("blood_pressure_diastolic", d, "d")):
            con.execute("INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                        "VALUES ('1',?,?,?,?,?,'mmHg')", (code, f"{tag}{i}", t, t, v))
    con.commit()
    con.close()
    return path


def test_error_finding_never_carries_the_exception_message():
    def boom(db, ref):
        raise RuntimeError("secret-value-123 /home/someone/file")
    out = concerns.evaluate(":memory:", "2031-03-15", rules=[("boom", boom)])
    assert [f.id for f in out] == ["boom_error"]
    blob = " ".join(str(v) for v in out[0].to_dict().values())
    assert "secret-value-123" not in blob and "/home/" not in blob and "RuntimeError" in blob


def test_severe_reading_does_not_hide_a_stage2_average():
    readings = [(f"2031-03-{d:02d}T16:00:00Z", 146, 91) for d in (9, 10, 11, 12, 13)] + [("2031-03-15T02:00:00Z", 186, 124)]
    path = _bp_db("severe_plus_avg.sqlite", readings)
    ids = [f.id for f in concerns.evaluate(path, "2031-03-15")]
    assert ids[0] == "bp_severe" and "bp_stage2_avg" in ids, ids
