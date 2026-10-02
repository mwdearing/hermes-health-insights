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


# ---- a write in progress: transient read errors are retried, the DB is never opened writable ----

class _Flaky:
    """Real connection whose first `state['fails']` execute/cursor-execute calls raise `state['msg']`."""
    def __init__(self, real, state):
        self._real, self._state = real, state

    @property
    def row_factory(self):
        return self._real.row_factory

    @row_factory.setter
    def row_factory(self, v):
        self._real.row_factory = v

    def _maybe(self):
        if self._state["fails"] > 0:
            self._state["fails"] -= 1
            raise sqlite3.OperationalError(self._state["msg"])

    def execute(self, *a, **k):
        self._maybe()
        return self._real.execute(*a, **k)

    def cursor(self):
        outer, cur = self, self._real.cursor()

        class C:
            def execute(self, *a, **k):
                outer._maybe()
                cur.execute(*a, **k)
                return self

            def __iter__(self):
                return iter(cur)

            def __getattr__(self, n):
                return getattr(cur, n)
        return C()

    def __getattr__(self, n):
        return getattr(self._real, n)


@pytest.fixture
def flaky(monkeypatch):
    real = sqlite3.connect
    sleeps: list[float] = []
    state = {"fails": 0, "msg": "", "connects": 0}

    def connect(*a, **k):
        state["connects"] += 1
        return _Flaky(real(*a, **k), state)

    monkeypatch.setattr(sqlite_ro.sqlite3, "connect", connect)
    monkeypatch.setattr(sqlite_ro.time, "sleep", sleeps.append)
    return state, sleeps


@pytest.mark.parametrize("msg", ["attempt to write a readonly database", "database is locked", "disk I/O error"])
def test_first_query_failure_is_retried_on_a_fresh_connection(tmp_path, flaky, msg):
    state, sleeps = flaky
    db = tmp_path / "b.sqlite"
    _make(db)
    state.update(fails=2, msg=msg, connects=0)
    conn = sqlite_ro.connect_readonly(db)
    assert conn.execute("select x from t").fetchone() == (7,)
    assert state["connects"] == 3  # closed and reopened, not just re-executed
    assert sleeps == [0.2, 0.4]
    conn.close()


def test_cursor_execute_is_retried_too(tmp_path, flaky):
    state, sleeps = flaky
    db = tmp_path / "b.sqlite"
    _make(db)
    conn = sqlite_ro.connect_readonly(db)
    state.update(fails=1, msg="database is locked")
    cur = conn.cursor()
    cur.execute("select x from t")
    assert cur.fetchall() == [(7,)]
    assert sleeps == [0.2]
    conn.close()


def test_gives_up_after_five_attempts_and_raises_the_original_error(tmp_path, flaky):
    state, sleeps = flaky
    db = tmp_path / "b.sqlite"
    _make(db)
    conn = sqlite_ro.connect_readonly(db)
    state.update(fails=99, msg="attempt to write a readonly database", connects=0)
    with pytest.raises(sqlite3.OperationalError, match="readonly database"):
        conn.execute("select x from t")
    assert sleeps == [0.2, 0.4, 0.8, 1.6]
    assert state["connects"] == 4  # one reopen per wait


def test_other_errors_and_write_probes_are_not_retried(tmp_path, flaky):
    state, sleeps = flaky
    db = tmp_path / "b.sqlite"
    _make(db)
    conn = sqlite_ro.connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        conn.execute("select x from nope")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("create table probe (x)")  # the doctor's write probe must fail fast
    assert sleeps == []
    conn.close()


def test_row_factory_survives_a_reopen(tmp_path, flaky):
    state, sleeps = flaky
    db = tmp_path / "b.sqlite"
    _make(db)
    conn = sqlite_ro.connect_readonly(db)
    conn.row_factory = sqlite3.Row
    state.update(fails=1, msg="database is locked")
    row = conn.execute("select x from t").fetchone()
    assert row["x"] == 7
    conn.close()
