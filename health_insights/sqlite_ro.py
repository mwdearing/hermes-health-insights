"""Open a SQLite file read-only through a URI whose path is percent-encoded.

A raw `file:{path}?mode=ro` breaks for paths containing `?` or `#` (SQLite reads them as the query or fragment) and for
spaces. Every read-only connect in this package goes through here.
"""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote


def readonly_uri(path: str | os.PathLike) -> str:
    return f"file:{quote(os.path.abspath(os.fspath(path)))}?mode=ro"


def connect_readonly(path: str | os.PathLike) -> sqlite3.Connection:
    return sqlite3.connect(readonly_uri(path), uri=True)
