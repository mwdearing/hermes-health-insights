"""The synthetic demo database makes every headline command runnable without any real data."""
import sqlite3
from datetime import date

import pytest

from health_insights import cli, demo


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.sqlite"
    demo.build(str(path), days=60, end=date(2030, 6, 30), seed=7)
    return str(path)


def test_demo_is_deterministic(tmp_path):
    a, b = tmp_path / "a.sqlite", tmp_path / "b.sqlite"
    demo.build(str(a), days=20, end=date(2030, 6, 30), seed=3)
    demo.build(str(b), days=20, end=date(2030, 6, 30), seed=3)
    q = "SELECT type_code, start_time, value FROM samples ORDER BY type_code, start_time, value"
    assert sqlite3.connect(a).execute(q).fetchall() == sqlite3.connect(b).execute(q).fetchall()


def test_demo_has_the_core_metrics(demo_db):
    codes = {r[0] for r in sqlite3.connect(demo_db).execute("SELECT DISTINCT type_code FROM samples")}
    for needed in ("weight", "height", "resting_heart_rate", "blood_pressure_systolic", "dietary_energy_consumed"):
        assert needed in codes, codes


@pytest.mark.parametrize("argv", [
    ["coverage", "{db}", "--md"],
    ["weekly", "--db", "{db}", "--date", "2030-06-30"],
    ["nutrition", "--db", "{db}", "--date", "2030-06-30", "--days", "28"],
    ["concerns", "--db", "{db}", "--date", "2030-06-30"],
    ["bp", "{db}", "--date", "2030-06-30"],
])
def test_headline_commands_run_on_demo_data(demo_db, capsys, argv):
    rc = cli.main([a.format(db=demo_db) for a in argv])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip(), argv


def test_demo_command_writes_a_database(tmp_path, capsys):
    out = tmp_path / "x.sqlite"
    assert cli.main(["demo", "--out", str(out), "--days", "10"]) == 0
    assert out.exists()
    assert "demo" in capsys.readouterr().out.lower()
