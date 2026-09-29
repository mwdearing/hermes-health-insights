"""Tests for health_insights/ecg.py. FAKE ECG fixtures only; scratch files live in pytest's tmp_path.

Apple's export stores each ECG as apple_health_export/electrocardiograms/<name>.csv with header lines up to a
blank line, then one voltage sample per line. We only ever count the samples, never store the voltages.
"""
import contextlib
import io
import os
import sqlite3
import stat
import zipfile

import pytest

from health_insights import ecg


def ecg_csv(recorded, classification, n=1024, rate=512):
    """A fake Apple ECG CSV: header lines until the first all-numeric sample row, then `n` sample lines.

    The default layout mirrors a real Apple export: after the Sample Rate line there are two blank
    lines, then the Lead and Unit lines, then one more blank line, then the samples. The parser must
    keep reading key/value pairs past the first blank line to capture Lead/Unit.
    """
    head = [
        "Name,Test Person",
        "Date of Birth,1990-01-01",
        f"Recorded Date,{recorded}",
        f"Classification,{classification}",
        "Symptoms,None",
        "Software Version,2.0",
        'Device,"Watch7,1"',
        f"Sample Rate,{rate} Hz",
        "",
        "",
        "Lead,Lead I",
        "Unit,µV",
        "",
    ]
    return "\n".join(head + [str((i * 37) % 200 - 100) for i in range(n)]) + "\n"


def make_zip(path, ecgs, with_ecg_folder=True):
    """Write a fake export zip. `ecgs` is a list of (member_name, csv_text)."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("apple_health_export/export.xml", '<?xml version="1.0"?><HealthData></HealthData>')
        if with_ecg_folder:
            for name, text in ecgs:
                z.writestr(f"apple_health_export/electrocardiograms/{name}", text)
    return str(path)


def cli(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return [l for l in buf.getvalue().splitlines() if l.strip()]


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "ecg" / "ecg.sqlite")
    ecgs = [
        ("ecg_2030-03-10.csv", ecg_csv("2030-03-10 08:15:00 -0500", "Sinus Rhythm")),
        ("ecg_2030-03-11.csv", ecg_csv("2030-03-11 21:40:00 -0500", "Atrial Fibrillation", n=15360)),
        ("ecg_2030-03-11b.csv", ecg_csv("2030-03-11 21:50:00 -0500", "Inconclusive", n=100)),
    ]
    ecg.cmd_import(make_zip(tmp_path / "one.zip", ecgs), p)
    return p


def test_parse_metadata(tmp_path):
    """recorded_at -> UTC ISO, Chicago local date, classification, sample rate, count, duration, device."""
    p = str(tmp_path / "ecg.sqlite")
    ecg.cmd_import(make_zip(tmp_path / "one.zip",
                            [("a.csv", ecg_csv("2030-03-10 08:15:00 -0500", "Sinus Rhythm"))],
                            with_ecg_folder=True), p)
    con = sqlite3.connect(p)
    row = con.execute("select recorded_at, recorded_local_date, classification, sample_rate, sample_count, "
                      "duration_s, device, symptoms, software_version, lead, unit from ecg_recordings").fetchone()
    con.close()
    # 2030-03-10 08:15:00 -0500 -> 13:15:00Z
    assert row[0] == "2030-03-10T13:15:00+00:00"
    assert row[1] == "2030-03-10"
    assert row[2] == "Sinus Rhythm"
    assert row[3] == 512
    assert row[4] == 1024
    assert abs(row[5] - 2.0) < 1e-9  # 1024 / 512
    assert row[6] == "Watch7,1"
    assert row[7] == "None"
    assert row[8] == "2.0"
    assert row[9] == "Lead I"
    assert row[10] == "µV"


def test_import_counts_and_idempotence(tmp_path):
    p = str(tmp_path / "ecg.sqlite")
    ecgs = [("a.csv", ecg_csv("2030-03-10 08:15:00 -0500", "Sinus Rhythm")),
            ("b.csv", ecg_csv("2030-03-11 21:40:00 -0500", "Atrial Fibrillation", n=15360))]
    z = make_zip(tmp_path / "one.zip", ecgs)
    assert "2 new, 0 already present" in cli(ecg.cmd_import, z, p)[0]
    assert "0 new, 2 already present" in cli(ecg.cmd_import, z, p)[0]  # re-import: deduped


def test_import_no_ecg_folder(tmp_path):
    """A zip with no electrocardiograms/ folder imports 0 new and exits 0."""
    p = str(tmp_path / "ecg.sqlite")
    out = cli(ecg.cmd_import, make_zip(tmp_path / "x.zip", [], with_ecg_folder=False), p)
    assert out == ["ECG import: 0 new, 0 already present, 0 rejected"]


def test_malformed_csv_counted_as_rejected(tmp_path):
    """A malformed ECG CSV is counted as rejected, never crashes the import."""
    p = str(tmp_path / "ecg.sqlite")
    bad = "this is not a valid ecg header\nno recorded date here\n"
    out = cli(ecg.cmd_import, make_zip(tmp_path / "one.zip", [("bad.csv", bad)]), p)
    assert "1 rejected" in out[0]


def test_no_waveform_stored(db):
    """Only metadata is stored; no voltage/waveform column exists."""
    con = sqlite3.connect(db)
    cols = [c[1] for c in con.execute("pragma table_info(ecg_recordings)").fetchall()]
    con.close()
    assert not any("wave" in c or "voltage" in c or "samples_blob" in c for c in cols)
    assert "sample_count" in cols and "duration_s" in cols


def test_db_mode_0600(db):
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600


def test_db_dir_mode_0700(tmp_path):
    p = str(tmp_path / "ecg" / "ecg.sqlite")
    ecg.cmd_import(make_zip(tmp_path / "one.zip", [("a.csv", ecg_csv("2030-03-10 08:15:00 -0500", "Sinus Rhythm"))]), p)
    assert stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode) == 0o700


def test_digest_line_window(db):
    """Last 7 days up to --date: counts + classifications; silent when nothing is recent."""
    assert "3 recordings in the last 7 days" in cli(ecg.cmd_digest_line, db, "2030-03-12")[0]
    assert "Atrial Fibrillation" in cli(ecg.cmd_digest_line, db, "2030-03-12")[0]
    # Nothing in the 7 days up to 2030-05-01 -> silent.
    assert cli(ecg.cmd_digest_line, db, "2030-05-01") == []


def test_digest_line_missing_db(tmp_path):
    assert cli(ecg.cmd_digest_line, str(tmp_path / "nope.sqlite"), "2030-03-12") == []


def test_decimal_microvolt_samples_are_all_counted(tmp_path):
    """Real Apple ECG samples are decimals (e.g. -12.345), mostly negative. An integer-only sample regex found
    the first sample hundreds of lines late and undercounted a 30 s recording (14403 of 15360)."""
    import zipfile
    body = ecg_csv("2030-03-10 08:15:00 -0500", "Sinus Rhythm", n=0).rstrip("\n")
    samples = [f"{((i * 37) % 200 - 100) / 7:.3f}" for i in range(15360)]
    samples[500] = "12"                     # an occasional integer-looking sample
    z = tmp_path / "e.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("apple_health_export/export.xml", "<HealthData/>")
        zz.writestr("apple_health_export/electrocardiograms/ecg_1.csv", body + "\n" + "\n".join(samples) + "\n")
    recs, rejected = ecg.read_ecgs(str(z))
    assert not rejected and len(recs) == 1
    assert recs[0].sample_count == 15360 and abs(recs[0].duration_s - 30.0) < 1e-6
    assert recs[0].lead == "Lead I" and recs[0].unit == "µV"
