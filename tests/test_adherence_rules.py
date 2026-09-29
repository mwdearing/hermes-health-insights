"""Tests for health_insights.concern_rules.adherence_rules.

Synthetic data only — a fake medlog store in a temp directory, fake
medication ids ("fakepril" etc., matching the medication-tracker repo's own
test convention). No real paths, no real medication names or doses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date

import pytest

from health_insights.concern_rules import adherence_rules

MEDLOG = os.environ.get("TEST_MEDLOG_BIN", "medlog")
REF = date(2030, 6, 15)


def _env(tmp, now=None):
    e = dict(os.environ)
    e["MEDLOG_HOME"] = tmp
    e["MEDLOG_TZ"] = "America/Chicago"
    e["MEDLOG_STORE"] = "jsonl"
    e.pop("MEDLOG_V1_PATH", None)
    if now:
        e["MEDLOG_NOW"] = now
    return e


def _run(tmp, *argv, now=None):
    return subprocess.run([MEDLOG, *argv], cwd=tmp, env=_env(tmp, now),
                           capture_output=True, text=True, timeout=30)


@pytest.fixture
def store(tmp_path, monkeypatch):
    tmp = str(tmp_path)
    monkeypatch.setenv("MEDLOG_HOME", tmp)
    monkeypatch.setenv("MEDLOG_TZ", "America/Chicago")
    monkeypatch.setenv("MEDLOG_STORE", "jsonl")
    monkeypatch.delenv("MEDLOG_V1_PATH", raising=False)
    return tmp


def _add_daily(tmp, mid="fakepril", start="2030-06-13"):
    r = _run(tmp, "add-med", mid, "--name", "Fakepril", "--dose", "10", "--unit", "mg",
              "--time", "08:00", "--start", start, now=f"{start}T00:00:00")
    assert r.returncode == 0, r.stderr


def _log(tmp, mid, d, status="taken"):
    r = _run(tmp, "log", mid, "--status", status, "--date", d, "--time", "08:00",
              now=f"{d}T09:00:00")
    assert r.returncode == 0, r.stderr


# REF = 2030-06-15. A 3-day window (06-13..06-15) starting the same day the
# fake medication was added means every day in the window is a real expected
# dose slot, so counts are exact and never inflated by pre-existing history.

def test_no_findings_when_nothing_missed(store):
    _add_daily(store)
    for d in ("2030-06-13", "2030-06-14", "2030-06-15"):
        _log(store, "fakepril", d)
    assert adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3) == []


def test_single_miss_not_flagged(store):
    _add_daily(store)
    for d in ("2030-06-13", "2030-06-14"):
        _log(store, "fakepril", d)
    # 06-15 (today) has no log yet -> exactly 1 missing, below the repeat threshold.
    results = adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3)
    assert results == [], results


def test_repeated_misses_flagged(store):
    _add_daily(store)
    _log(store, "fakepril", "2030-06-13")
    # 06-14 and 06-15 both unlogged -> 2 missing, at the repeat threshold.
    results = adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3)
    assert [f.id for f in results] == ["medication_adherence"], results


def test_finding_shape_counts_only_no_medication_name(store):
    _add_daily(store)
    _log(store, "fakepril", "2030-06-13")
    f = adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3)[0]
    d = f.to_dict()
    assert d["level"] == 1
    assert "Fakepril" not in d["evidence"] and "fakepril" not in d["evidence"]
    assert "2" in d["evidence"] and "3 days" in d["evidence"]
    assert "no dosing advice" in d["advice"].lower()
    assert "medlog" in d["source"].lower()


def test_ref_date_accepts_date_object_and_string(store):
    _add_daily(store)
    _log(store, "fakepril", "2030-06-13")
    a = adherence_rules.adherence_findings("ignored.sqlite", REF, days=3)
    b = adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3)
    assert [f.id for f in a] == [f.id for f in b] == ["medication_adherence"]


def test_empty_store_no_findings(store):
    assert adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3) == []


def test_medlog_missing_binary_returns_none_gracefully(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDLOG_BIN", str(tmp_path / "no-such-medlog-binary"))
    assert adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat()) == []


def test_days_parameter_changes_the_window(store):
    _add_daily(store)
    _log(store, "fakepril", "2030-06-13")
    # `medlog missing --days N` covers ref-N..ref inclusive. Same store, same
    # 2 real misses (06-14, 06-15): days=0 (today only) sees just 1 miss,
    # below the repeat threshold; days=3 sees both.
    assert adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=0) == []
    results = adherence_rules.adherence_findings("ignored.sqlite", REF.isoformat(), days=3)
    assert [f.id for f in results] == ["medication_adherence"]


def test_via_concerns_evaluate_registered():
    from health_insights import concerns
    names = [n for n, _ in concerns.DEFAULT_RULES]
    assert "adherence" in names
