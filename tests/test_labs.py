"""Tests for health_insights/labs.py. FAKE FHIR data only; scratch files live in pytest's tmp_path."""
import contextlib
import io
import json
import os
import sqlite3
import stat
import zipfile
from datetime import date, timedelta

import pytest

from health_insights import labs


def obs(i, code, day, qty=None, unit=None, string=None, rng=None, rtext=None, comp=False):
    d = {"resourceType": "Observation", "id": i, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
         "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": "Test"}], "text": "Test"},
         "effectiveDateTime": f"{day}T08:00:00-05:00", "issued": f"{day}T12:00:00Z"}
    if qty is not None:
        d["valueQuantity"] = {"value": qty, "unit": unit}
    if string is not None:
        d["valueString"] = string
    r = {}
    if rng:
        r["low"] = {"value": rng[0]}; r["high"] = {"value": rng[1]}
    if rtext:
        r["text"] = rtext
    if r:
        d["referenceRange"] = [r]
    if comp:
        d["component"] = [{"code": {"text": "part"}, "valueQuantity": {"value": 1, "unit": "x"}}]
    return d


def make_zip(path, records, extras=True):
    with zipfile.ZipFile(path, "w") as z:
        for r in records:
            z.writestr(f"apple_health_export/clinical-records/{r['id']}.json", json.dumps(r))
        if extras:
            z.writestr("apple_health_export/clinical-records/m.json", json.dumps({"resourceType": "MedicationRequest", "id": "m"}))
            z.writestr("apple_health_export/clinical-records/d.json", json.dumps({"resourceType": "DocumentReference", "id": "d"}))
            z.writestr("apple_health_export/export.xml", "<HealthData/>")
    return str(path)


def cli(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return [l for l in buf.getvalue().splitlines() if l.strip()]


RECS = [obs("o1", "2345-7", "2030-01-10", 110, "mg/dL", rng=(70, 99)), obs("o2", "2345-7", "2030-02-10", 90, "mg/dL", rng=(70, 99)),
        obs("o3", "2345-7", "2030-03-10", 120, "mg/dL", rng=(70, 99)), obs("o4", "718-7", "2030-03-10", 10.0, "g/dL", rng=(12, 16)),
        obs("o5", "4548-4", "2030-03-10", string="5.4", rtext="4.0-6.0"), obs("o6", "5778-6", "2030-03-10", string="Negative"),
        obs("o7", "9999-9", "2030-03-10", comp=True), obs("o10", "2093-3", "2030-02-10", 250, "mg/dL", rtext="<200")]


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "labs" / "labs.sqlite")
    labs.cmd_import(make_zip(tmp_path / "one.zip", RECS), p)
    return p


def test_import_counts_and_idempotence(tmp_path):
    p = str(tmp_path / "labs.sqlite"); z = make_zip(tmp_path / "one.zip", RECS)
    assert "8 records read, 8 new, 0 already present" in cli(labs.cmd_import, z, p)[0]
    assert "8 records read, 0 new, 8 already present" in cli(labs.cmd_import, z, p)[0]


def test_other_resource_types_ignored(tmp_path):
    p = str(tmp_path / "labs.sqlite")
    out = cli(labs.cmd_import, make_zip(tmp_path / "x.zip", RECS[:1], extras=True), p)[0]
    assert "1 records read" in out


def test_flags_from_quantity_range_and_text(db):
    import sqlite3
    flags = dict(sqlite3.connect(db).execute("select obs_id, flag from lab_results").fetchall())
    assert flags["o1"] == "HIGH" and flags["o2"] == "NORMAL" and flags["o4"] == "LOW"
    assert flags["o5"] == "NORMAL"      # numeric string against a "4.0-6.0" text range
    assert flags["o10"] == "HIGH"       # "<200" gives only an upper bound
    assert flags["o6"] == "UNKNOWN" and flags["o7"] == "UNKNOWN"


def test_summary_line(db):
    assert cli(labs.cmd_summary, db)[0] == "Labs: 8 results on 3 dates, latest 2030-03-10; 6 numeric; 4 outside range (HIGH 3, LOW 1)"


def test_trend_up_and_single_value(db):
    assert cli(labs.cmd_trend, db, "2345-7")[0] == "2345-7: 3 values, latest vs previous: up"
    assert cli(labs.cmd_trend, db, "718-7")[0] == "718-7: 1 values, latest vs previous: n/a"


def test_trend_flat_and_down(tmp_path):
    p = str(tmp_path / "l.sqlite")
    recs = [obs("ra", "1-1", "2030-01-01", 5, "x"), obs("rb", "1-1", "2030-02-01", 5, "x"), obs("rc", "2-2", "2030-01-01", 9, "x"), obs("rd", "2-2", "2030-02-01", 4, "x")]
    labs.cmd_import(make_zip(tmp_path / "t.zip", recs), p)
    assert cli(labs.cmd_trend, p, "1-1")[0].endswith("flat")
    assert cli(labs.cmd_trend, p, "2-2")[0].endswith("down")


def test_digest_line_window(db):
    today = date.today().isoformat()
    assert cli(labs.cmd_digest_line, db, today) == ["Labs: 8 results imported in the last 7 days, 4 outside range"]
    assert cli(labs.cmd_digest_line, db, (date.today() + timedelta(days=30)).isoformat()) == []


def test_file_and_dir_modes(db):
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(db)).st_mode) == 0o700


def test_second_export_adds_only_new(tmp_path):
    p = str(tmp_path / "l.sqlite")
    labs.cmd_import(make_zip(tmp_path / "one.zip", RECS), p)
    z2 = make_zip(tmp_path / "two.zip", [RECS[0], obs("o9", "3016-3", "2030-04-10", 5.0, "mIU/L", rng=(0.4, 4.0))])
    assert "2 records read, 1 new, 1 already present" in cli(labs.cmd_import, z2, p)[0]
    assert "9 results on 4 dates, latest 2030-04-10" in cli(labs.cmd_summary, p)[0]


def test_open_db_tightens_permissions_on_an_existing_permissive_file(tmp_path):
    """open_db() must tighten an EXISTING file's mode, not just rely on umask for new-file creation (review
    finding 7, 2026-09-22): umask only governs files sqlite creates fresh, so a db file that already existed
    with a permissive mode (copied in, or created before this code existed) stayed permissive forever."""
    d = tmp_path / "labs"
    d.mkdir(mode=0o755)
    p = d / "labs.sqlite"
    p.touch()
    p.chmod(0o644)  # simulate a pre-existing, permissive file
    os.chmod(d, 0o755)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644  # confirm the starting condition
    conn = labs.open_db(str(p))
    conn.close()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600, "open_db must tighten an existing file, not just new ones"
    # The pre-existing directory the caller named keeps its own mode (full review 2026-09-22); only a directory
    # this code creates, or the live labs dir, is made 0700.
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o755


def test_open_db_leaves_a_pre_existing_foreign_directory_mode_alone(tmp_path):
    """Full review 2026-09-22: ensure_db_dir chmod'ed whatever parent directory the caller named. Only a
    directory this code creates (or the live labs dir) is made owner-only."""
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(d, 0o755)
    labs.open_db(str(d / "labs.sqlite")).close()
    assert oct(os.stat(d).st_mode & 0o777) == "0o755"
    new = tmp_path / "fresh" / "labs.sqlite"
    labs.open_db(str(new)).close()
    assert oct(os.stat(new.parent).st_mode & 0o777) == "0o700"


def test_digest_line_counts_by_chicago_date_not_utc(db):
    """A result imported at 22:30 Chicago is already the next UTC day; the digest for the Chicago date must
    still count it (full review 2026-09-22)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    chi = ZoneInfo("America/Chicago")
    today = datetime.now(chi).date()
    late = datetime(today.year, today.month, today.day, 22, 30, tzinfo=chi).astimezone(timezone.utc).isoformat()
    con = sqlite3.connect(db)
    con.execute("UPDATE lab_results SET imported_at = ?", (late,))
    con.commit()
    con.close()
    assert cli(labs.cmd_digest_line, db, today.isoformat()) == ["Labs: 8 results imported in the last 7 days, 4 outside range"]
