"""Open a SQLite file read-only through a URI whose path is percent-encoded.

A raw `file:{path}?mode=ro` breaks for paths containing `?` or `#` (SQLite reads them as the query or fragment) and for
spaces. Every read-only connect in this package goes through here.
"""
from __future__ import annotations

import os
import sqlite3
import time
from urllib.parse import quote


def readonly_uri(path: str | os.PathLike) -> str:
    return f"file:{quote(os.path.abspath(os.fspath(path)))}?mode=ro"


DELAYS = (0.2, 0.4, 0.8, 1.6)  # 5 attempts over about 3 seconds
TRANSIENT = ("readonly database", "database is locked", "disk i/o error")
_READS = ("select", "pragma", "with", "explain")


def is_transient(exc: BaseException) -> bool:
    """The receiver is mid-write: a read-only connection cannot roll its rollback journal back (or the file is busy)."""
    return isinstance(exc, sqlite3.OperationalError) and any(t in str(exc).lower() for t in TRANSIENT)


def _is_read(sql) -> bool:
    return isinstance(sql, str) and sql.lstrip().lower().startswith(_READS)


class RetryingReadOnlyConnection:
    """A read-only connection whose read statements survive a write in progress.

    The failure happens on the first statement, not on connect(), so a read that fails with a transient error closes
    the connection, waits (0.2, 0.4, 0.8, 1.6 s) and reopens it read-only, up to 5 attempts, then raises the original
    error. Write statements (the doctor's write probe) are never retried. Never writable, never ``immutable``.
    """

    def __init__(self, path):
        self._uri = readonly_uri(path)
        self._row_factory = None
        self._conn = sqlite3.connect(self._uri, uri=True)

    def _reopen(self):
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
        self._conn = sqlite3.connect(self._uri, uri=True)
        if self._row_factory is not None:
            self._conn.row_factory = self._row_factory

    def _run(self, sql, call):
        """call(conn) -> result; retried with a fresh connection when `sql` is a read and the error is transient."""
        if not _is_read(sql):
            return call(self._conn)
        for delay in DELAYS:
            try:
                return call(self._conn)
            except sqlite3.OperationalError as exc:
                if not is_transient(exc):
                    raise
            time.sleep(delay)
            try:
                self._reopen()
            except sqlite3.OperationalError as exc:
                if not is_transient(exc):
                    raise
        return call(self._conn)

    def execute(self, sql, *args):
        return self._run(sql, lambda c: c.execute(sql, *args))

    def executemany(self, sql, *args):
        return self._conn.executemany(sql, *args)

    def cursor(self, *args):
        return _RetryingCursor(self, self._conn.cursor(*args))

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._row_factory = value
        self._conn.row_factory = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _RetryingCursor:
    def __init__(self, owner, cur):
        self._owner, self._cur = owner, cur

    def execute(self, sql, *args):
        def call(conn):
            if conn is not self._cur.connection:  # the connection was reopened: take a cursor on the new one
                self._cur = conn.cursor()
            return self._cur.execute(sql, *args)
        self._owner._run(sql, call)
        return self

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def connect_readonly(path: str | os.PathLike):
    return RetryingReadOnlyConnection(path)
