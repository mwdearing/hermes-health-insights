"""health-insights doctor: loud errors for a wrong or empty database, counts and dates only. Synthetic data."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from health_insights import cli, settings

SCHEMA = (
    "create table samples (sample_id integer primary key, source_id integer, type_code text, client_record_id text,"
    " start_time text, end_time text, value real, unit text, metadata_json text);"
    "create table sleep_sessions (sleep_session_id integer primary key, source_id integer, client_record_id text,"
    " start_time text, end_time text);"
)


def _iso(days_ago, hour=12):
    # The doctor counts days in the configured timezone, so build the stamps from that zone's calendar date. Using the
    # UTC date made "today" a future day (and the count 29, not 30) between 19:00 and midnight Chicago time.
    local_day = datetime.now(settings.timezone()).date() - timedelta(days=days_ago)
    local = datetime.combine(local_day, datetime.min.time()).replace(hour=hour, tzinfo=settings.timezone())
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_db(path, days_by_type):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    n = 0
    for code, days in days_by_type.items():
        for d in days:
            n += 1
            if code == "sleep":
                con.execute("insert into sleep_sessions values (?,?,?,?,?)", (n, 1, f"s{n}", _iso(d, 4), _iso(d, 10)))
                continue
            con.execute("insert into samples values (?,?,?,?,?,?,?,?,?)",
                        (n, 1, code, f"r{n}", _iso(d), _iso(d), 1.0, "u", "{}"))
    con.commit()
    con.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("HEALTH_INSIGHTS_BRIDGE_DB", "HEALTH_INSIGHTS_DB", "HEALTH_INSIGHTS_STALE_DAYS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.config/healthrelay/db-path
    settings.reset()
    yield
    settings.reset()


def doctor(capsys, *argv):
    code = cli.main(["doctor", *argv])
    return code, capsys.readouterr()


FULL = {c: range(0, 30) for c in ("resting_heart_rate", "heart_rate_variability_sdnn", "weight", "steps",
                                   "blood_pressure_systolic", "hydration", "sleep")}


def test_ok_exit_0(tmp_path, capsys):
    db = tmp_path / "b.sqlite"
    make_db(db, FULL)
    code, out = doctor(capsys, "--db", str(db))
    assert code == 0, out.out
    assert "status: ok" in out.out
    assert "opened_read_only: True" in out.out
    assert "resting_heart_rate: last sample" in out.out


def test_config_file_is_the_source(tmp_path, capsys, monkeypatch):
    db = tmp_path / "b.sqlite"
    make_db(db, FULL)
    (tmp_path / "config.yaml").write_text(f"bridge_db: {db}\n")
    settings.reset()
    code, out = doctor(capsys)
    assert code == 0
    assert "config.yaml" in out.out and "[from bridge_db in" in out.out


def test_unset_is_error_2(capsys):
    code, out = doctor(capsys)
    assert code == 2
    assert "ERROR: bridge_db is not set" in out.out


def test_missing_is_error_2(tmp_path, capsys):
    code, out = doctor(capsys, "--db", str(tmp_path / "gone.sqlite"))
    assert code == 2
    assert "not found" in out.out


def test_zero_byte_is_error_2_and_untouched(tmp_path, capsys):
    db = tmp_path / "zero.sqlite"
    db.write_bytes(b"")
    code, out = doctor(capsys, "--db", str(db))
    assert code == 2
    assert "0-byte" in out.out
    assert db.stat().st_size == 0


def test_not_a_bridge_database_is_error_2(tmp_path, capsys):
    db = tmp_path / "x.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table other (a)")
    con.commit()
    con.close()
    code, out = doctor(capsys, "--db", str(db))
    assert code == 2
    assert "no samples table" in out.out


def test_garbage_file_is_error_2(tmp_path, capsys):
    db = tmp_path / "junk.sqlite"
    db.write_bytes(b"this is not sqlite" * 20)
    code, out = doctor(capsys, "--db", str(db))
    assert code == 2


def test_stale_is_warning_1(tmp_path, capsys):
    db = tmp_path / "b.sqlite"
    make_db(db, {c: range(10, 40) for c in FULL})
    code, out = doctor(capsys, "--db", str(db))
    assert code == 1
    assert "older than 3 days" in out.out


def test_stale_days_option_and_env(tmp_path, capsys, monkeypatch):
    db = tmp_path / "b.sqlite"
    make_db(db, {c: range(5, 35) for c in FULL})
    assert doctor(capsys, "--db", str(db))[0] == 1
    assert doctor(capsys, "--db", str(db), "--stale-days", "10")[0] == 0
    monkeypatch.setenv("HEALTH_INSIGHTS_STALE_DAYS", "10")
    assert doctor(capsys, "--db", str(db))[0] == 0


def test_sparse_metric_is_warning_1(tmp_path, capsys):
    data = dict(FULL)
    data["weight"] = [45]  # recorded long ago, nothing in the last 30 days
    db = tmp_path / "b.sqlite"
    make_db(db, data)
    code, out = doctor(capsys, "--db", str(db))
    assert code == 1
    assert "weight: sparse" in out.out


def test_path_with_question_mark_and_hash(tmp_path, capsys):
    d = tmp_path / "we?ird #dir"
    d.mkdir()
    db = d / "b.sqlite"
    make_db(db, FULL)
    code, out = doctor(capsys, "--db", str(db))
    assert code == 0, out.out


def test_json_output(tmp_path, capsys):
    db = tmp_path / "b.sqlite"
    make_db(db, FULL)
    code, out = doctor(capsys, "--db", str(db), "--json")
    data = json.loads(out.out)
    assert code == 0 and data["status"] == "ok"
    assert data["bridge_db"]["opened_read_only"] is True
    assert data["metrics"]["resting_heart_rate"]["days_with_data_30"] == 30
    assert "value" not in json.dumps(data["metrics"])  # dates and counts only


def test_coverage_refuses_a_zero_byte_database(tmp_path, capsys):
    db = tmp_path / "zero.sqlite"
    db.write_bytes(b"")
    code = cli.main(["coverage", "--db", str(db)])
    assert code == 2
    assert "0-byte" in capsys.readouterr().err


INTAKE_SCHEMA = (
    "create table intake_producers (producer_id text, display_label text, revoked_at text);"
    "create table intake_context_tokens (token_prefix text, label text, revoked_at text);"
    "create table intake_state (intake_id text, writer_bundle_id text, deleted integer, updated_at text);"
)


def make_intake(db, populated=True):
    with sqlite3.connect(db) as con:
        con.executescript(INTAKE_SCHEMA)
        if populated:
            con.executemany("insert into intake_producers values (?,?,?)", [
                ("synthetic-producer-a", "Label Alpha", None),
                ("synthetic-producer-b", "Label Beta", "2026-10-01T12:00:00Z"),
            ])
            con.executemany("insert into intake_context_tokens values (?,?,?)", [
                ("hri_aaaa", "tok one", None), ("hri_bbbb", "tok two", None),
                ("hri_cccc", "tok three", "2026-10-01T12:00:00Z"),
            ])
            con.executemany("insert into intake_state values (?,?,?,?)", [
                ("intake-1", "dev.example.a", 0, "2026-10-01T12:00:00Z"),
                ("intake-2", "dev.example.a", 0, "2026-10-01T13:00:00Z"),
                ("intake-3", "dev.example.a", 0, "2026-10-01T14:00:00Z"),
                ("intake-4", "dev.example.a", 1, "2026-10-03T02:00:00Z"),
            ])


@pytest.mark.parametrize("missing", [None, "intake_producers", "intake_context_tokens", "intake_state"])
def test_intake_schema_absent(tmp_path, capsys, missing):
    db = tmp_path / "b.sqlite"
    make_db(db, FULL)
    if missing:
        make_intake(db)
        with sqlite3.connect(db) as con:
            con.execute(f"drop table {missing}")
    code, out = doctor(capsys, "--db", str(db), "--json")
    report = json.loads(out.out)
    assert code == 0
    assert report["intake_context"] == {"schema": "absent"}
    assert report["errors"] == report["warnings"] == []


def test_intake_ready_counts_and_local_newest_date(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HEALTH_INSIGHTS_TZ", "America/Chicago")
    settings.reset()
    db = tmp_path / "b.sqlite"
    make_db(db, FULL)
    make_intake(db)
    before = db.read_bytes()
    code, out = doctor(capsys, "--db", str(db), "--json")
    assert code == 0
    assert json.loads(out.out)["intake_context"] == {
        "schema": "ready", "producers_active": 1, "tokens_active": 2,
        "intakes_active": 3, "newest_intake_date": "2026-10-02",
    }
    assert db.read_bytes() == before
    for private in ("synthetic-producer", "Label Alpha", "tok one", "hri_", "intake-1", "dev.example"):
        assert private not in out.out


@pytest.mark.parametrize("days,expected_code", [(FULL, 0), ({c: range(10, 40) for c in FULL}, 1), ({}, 2)])
def test_intake_text_counts_only_and_exit_unchanged(tmp_path, capsys, monkeypatch, days, expected_code):
    monkeypatch.setenv("HEALTH_INSIGHTS_TZ", "America/Chicago")
    settings.reset()
    db = tmp_path / "b.sqlite"
    make_db(db, days)
    plain_code, plain = doctor(capsys, "--db", str(db))
    assert plain_code == expected_code
    assert "intake context: not set up (receiver older than migration 013)" in plain.out
    make_intake(db)
    code, out = doctor(capsys, "--db", str(db))
    assert code == plain_code
    assert "intake context: ready, 1 active producer(s), 2 active token(s), 3 intake(s), newest 2026-10-02" in out.out
    for private in ("synthetic-producer", "Label Alpha", "Label Beta", "tok one", "hri_", "intake-1", "dev.example"):
        assert private not in out.out


def test_intake_ready_empty_tables(tmp_path, capsys):
    db = tmp_path / "b.sqlite"
    make_db(db, FULL)
    make_intake(db, populated=False)
    code, out = doctor(capsys, "--db", str(db), "--json")
    assert code == 0
    assert json.loads(out.out)["intake_context"] == {
        "schema": "ready", "producers_active": 0, "tokens_active": 0,
        "intakes_active": 0, "newest_intake_date": None,
    }
