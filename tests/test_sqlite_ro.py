import sqlite3
from pathlib import Path

import pytest

from health_insights import sqlite_ro


def _make(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("create table t (x integer)")
    conn.execute("insert into t values (7)")
    conn.commit()
    conn.close()


def test_uri_percent_encodes_query_fragment_and_space_characters():
    uri = sqlite_ro.readonly_uri("/data/a b?c#d.sqlite")
    assert uri == "file:/data/a%20b%3Fc%23d.sqlite?mode=ro"


def test_relative_path_becomes_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sqlite_ro.readonly_uri("x.sqlite") == f"file:{tmp_path}/x.sqlite?mode=ro".replace(" ", "%20")


@pytest.mark.parametrize("name", ["plain.sqlite", "with space.sqlite", "q?mark.sqlite", "hash#tag.sqlite", "a?b#c d.sqlite"])
def test_connect_readonly_opens_the_right_file_and_cannot_write(tmp_path, name):
    db = tmp_path / name
    _make(db)
    conn = sqlite_ro.connect_readonly(db)
    try:
        assert conn.execute("select x from t").fetchone() == (7,)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("insert into t values (8)")
    finally:
        conn.close()


def test_missing_file_is_an_error_not_a_new_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        sqlite_ro.connect_readonly(tmp_path / "nope?x.sqlite")
    assert not list(tmp_path.iterdir())


def test_every_mode_ro_connect_goes_through_the_helper():
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for folder in ("health_insights", "scripts"):
        for py in (root / folder).rglob("*.py"):
            if py.name == "sqlite_ro.py":
                continue
            for n, line in enumerate(py.read_text().splitlines(), 1):
                if "mode=ro" in line:
                    offenders.append(f"{py.relative_to(root)}:{n}")
    assert offenders == []
