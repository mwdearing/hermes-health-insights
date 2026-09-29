"""Keep every scratch file under the repo (never /tmp): pytest's tmp_path via --basetemp in pytest.ini,
and the tempfile module via tempfile.tempdir. Synthetic data only lives here; the directory is gitignored."""
import os
import tempfile
from pathlib import Path

import pytest

os.environ["HEALTH_INSIGHTS_TZ"] = "America/Chicago"
os.environ["HEALTH_INSIGHTS_WEIGHT_UNIT"] = "lb"  # fixtures were written for pounds
os.environ["HEALTH_INSIGHTS_MEDLOG"] = "1"  # the suite exercises the medlog integration on synthetic data only
os.environ["HEALTH_INSIGHTS_CONFIG"] = "/nonexistent/health-insights-test.yaml"  # never read a real deployment file  # tests pin the zone the fixtures were written in
SCRATCH = Path(__file__).resolve().parent / ".scratch_tmp"
SCRATCH.mkdir(exist_ok=True)
os.chmod(SCRATCH, 0o700)
os.environ["HEALTH_INSIGHTS_DATA_DIR"] = str(SCRATCH / "data")  # tests never touch a real data directory
tempfile.tempdir = str(SCRATCH)


@pytest.fixture(autouse=True)
def _no_real_medlog(monkeypatch, tmp_path_factory):
    """Blanket safety net (2026-09-27): concern_rules/adherence_rules.py shells out to the
    real `medlog` CLI, which defaults to the user's real medication log (MEDLOG_HOME). Any
    test that reaches `concerns.evaluate()` with the real DEFAULT_RULES — including tests
    written before this rule existed, like test_concerns.py — must never touch that real
    data or depend on its current (changing) state. Point MEDLOG_HOME at an empty synthetic
    directory for every test unless the test explicitly overrides it itself."""
    empty = tmp_path_factory.mktemp("no-medlog-here")
    monkeypatch.setenv("MEDLOG_HOME", str(empty))
    monkeypatch.setenv("MEDLOG_STORE", "jsonl")
    monkeypatch.delenv("MEDLOG_V1_PATH", raising=False)
