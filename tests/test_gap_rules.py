"""Tests for the data-gap concern rule.

Synthetic data only — no real paths, no real DB.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, timedelta

import pytest

from health_insights.bridge_db import _normalize_ts, open_readonly
from health_insights.weekly import METRIC_LABELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REF = date(2031, 9, 10)  # fixed reference date


def _make_db(samples: dict[str, list[int]]) -> str:
    """Create a temp DB with one sample per listed day before _REF.

    *samples*: {type_code: [days_before_ref, ...]}
    Returns the absolute path to the created .sqlite file.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite", prefix="gap_test_")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE samples (
            sample_id INTEGER PRIMARY KEY,
            source_id TEXT,
            type_code TEXT,
            client_record_id TEXT,
            start_time TEXT,
            end_time TEXT,
            value REAL,
            unit TEXT,
            metadata_json TEXT,
            UNIQUE (type_code, start_time, value)
        )"""
    )
    for tc, days in samples.items():
        for d in days:
            t = (_REF - timedelta(days=d)).strftime("%Y-%m-%dT17:00:00Z")
            con.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES ('1', ?, ?, ?, ?, 42, 'x')",
                (tc, f"s{tc}{d}", t, t),
            )
    con.commit()
    con.close()
    return path


def _make_no_table_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".sqlite", prefix="gap_test_")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Config that includes all METRIC_LABELS keys
_FULL_CONFIG = {
    "timezone": "America/Chicago",
    "metrics": {
        "resting_heart_rate": {},
        "heart_rate_variability_sdnn": {},
        "weight": {},
        "oxygen_saturation": {},
        "sleep_analysis": {},
    },
}

# Config with only one metric (to test isolation)
_ONE_METRIC_CONFIG = {
    "timezone": "America/Chicago",
    "metrics": {"resting_heart_rate": {}},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSingleSilentMetric:
    def test_one_silent_metric(self):
        """A metric silent for 5 days should produce one data_gap finding."""
        db_path = _make_db({"resting_heart_rate": list(range(15, 29))})
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG)
            assert len(findings) == 1
            assert findings[0].id == "data_gap"
            assert findings[0].level == 1
            assert "Resting HR" in findings[0].evidence
            assert "5" in findings[0].evidence
            assert findings[0].source == "heuristic"
            assert "clinician" not in findings[0].advice.lower()
        finally:
            os.unlink(db_path)

    def test_two_silent_metrics_named_together(self):
        """Two silent metrics should appear in the same finding's evidence."""
        db_path = _make_db({
            "resting_heart_rate": list(range(15, 29)),
            "heart_rate_variability_sdnn": list(range(15, 30)),
            "weight": list(range(15, 29)),
            "oxygen_saturation": list(range(15, 29)),
        })
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG)
            assert len(findings) == 1
            ev = findings[0].evidence
            assert "Resting HR" in ev
            assert "HRV" in ev
        finally:
            os.unlink(db_path)


class TestBoundaryConditions:
    def test_exactly_three_days_not_silent(self):
        """Most recent sample exactly 3 days before ref_date is NOT silent."""
        db_path = _make_db({"resting_heart_rate": [3] + list(range(4, 18))})
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_ONE_METRIC_CONFIG)
            assert len(findings) == 0
        finally:
            os.unlink(db_path)

    def test_exactly_four_days_is_silent(self):
        """Most recent sample exactly 4 days before ref_date IS silent."""
        db_path = _make_db({"resting_heart_rate": [4] + list(range(5, 19))})
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_ONE_METRIC_CONFIG)
            assert len(findings) == 1
            assert "4" in findings[0].evidence
        finally:
            os.unlink(db_path)


class TestUntrackedAndThinHistory:
    def test_metric_never_tracked_not_flagged(self):
        """A type_code with no rows ever should not appear as silent — it's untracked."""
        db_path = _make_db({
            "heart_rate_variability_sdnn": list(range(15, 20)),
            "weight": list(range(15, 20)),
            "oxygen_saturation": list(range(15, 20)),
        })
        try:
            from health_insights.concern_rules import gap_rules
            # resting_heart_rate is NOT in samples at all
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG)
            # Only metrics that HAVE rows can be silent
            assert len(findings) == 0
        finally:
            os.unlink(db_path)

    def test_too_little_total_history_not_flagged(self):
        """A metric with fewer than 14 distinct days total should not be flagged."""
        db_path = _make_db({"resting_heart_rate": [10, 11]})
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_ONE_METRIC_CONFIG)
            assert len(findings) == 0
        finally:
            os.unlink(db_path)


class TestMissingTableAndEmpty:
    def test_missing_samples_table_no_crash(self):
        """A DB with no samples table should return [] without crashing."""
        db_path = _make_no_table_db()
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG)
            assert findings == []
        finally:
            os.unlink(db_path)

    def test_empty_config_no_findings(self):
        """An empty metrics config produces no findings."""
        db_path = _make_db({"resting_heart_rate": list(range(15, 20))})
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config={"metrics": {}})
            assert findings == []
        finally:
            os.unlink(db_path)


class TestConfigNone:
    def test_config_none_loads_default(self):
        """When config=None, the function should load config/health_monitor.yaml and still work."""
        # Create a synthetic DB where no metric is silent (all fresh)
        db_path = _make_db({
            "resting_heart_rate": list(range(15, 20)),
            "heart_rate_variability_sdnn": list(range(15, 20)),
            "weight": list(range(15, 20)),
            "oxygen_saturation": list(range(15, 20)),
        })
        try:
            from health_insights.concern_rules import gap_rules
            # config=None means it loads the real default config
            findings = gap_rules.data_gap_findings(db_path, _REF, config=None)
            # With all fresh, should be no findings
            assert findings == []
        finally:
            os.unlink(db_path)


class TestAllFresh:
    def test_all_fresh_no_findings(self):
        """When all configured metrics have data today, no findings."""
        db_path = _make_db({
            "resting_heart_rate": list(range(15, 20)),
            "heart_rate_variability_sdnn": list(range(15, 20)),
            "weight": list(range(15, 20)),
            "oxygen_saturation": list(range(15, 20)),
        })
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG)
            assert findings == []
        finally:
            os.unlink(db_path)


class TestNormalizeTsReused:
    def test_normalize_ts_is_reused(self):
        """Verify _normalize_ts from bridge_db works as expected for a known timestamp."""
        ts = "2031-09-05T17:00:00Z"
        result = _normalize_ts(ts)
        assert result is not None
        assert "+00:00" in result


class TestNonExistentDb:
    def test_nonexistent_db_path_no_crash(self):
        """A path that doesn't exist should return [] without crashing."""
        from health_insights.concern_rules import gap_rules
        findings = gap_rules.data_gap_findings("/nonexistent/path.db", _REF, config=_FULL_CONFIG)
        assert findings == []


class TestEvidenceFormat:
    def test_evidence_joined_with_semicolons(self):
        """Evidence should join silent metrics with '; ' separator."""
        db_path = _make_db({
            "resting_heart_rate": list(range(15, 29)),
            "heart_rate_variability_sdnn": list(range(15, 30)),
            "weight": list(range(15, 29)),
            "oxygen_saturation": list(range(15, 29)),
        })
        try:
            from health_insights.concern_rules import gap_rules
            findings = gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG)
            assert len(findings) == 1
            ev = findings[0].evidence
            assert "; " in ev
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Store-level staleness (1.5)
# ---------------------------------------------------------------------------


class TestStoreStaleness:
    def test_fires_for_a_short_history_the_14_day_rule_ignores(self):
        from health_insights.concern_rules import gap_rules

        db_path = _make_db({"steps": [8, 9, 10]})  # 3 days of history, newest 8 days old
        try:
            assert gap_rules.data_gap_findings(db_path, _REF, config=_FULL_CONFIG) == []
            findings = gap_rules.store_staleness_findings(db_path, _REF)
            assert len(findings) == 1
            f = findings[0]
            assert f.id == "store_stale"
            assert f.level == 2
            assert f.title == f"No new health data since {(_REF - timedelta(days=8)).isoformat()}"
            assert "newest record 8 days old" in f.evidence
            assert "phone" in f.advice.lower() and "receiver" in f.advice.lower()
        finally:
            os.unlink(db_path)

    def test_silent_when_data_is_recent(self):
        from health_insights.concern_rules import gap_rules

        db_path = _make_db({"steps": [1, 2, 3]})
        try:
            assert gap_rules.store_staleness_findings(db_path, _REF) == []
        finally:
            os.unlink(db_path)

    def test_boundary_three_days_is_not_stale(self):
        from health_insights.concern_rules import gap_rules

        db_path = _make_db({"steps": [3]})
        try:
            assert gap_rules.store_staleness_findings(db_path, _REF) == []
        finally:
            os.unlink(db_path)

    def test_empty_or_missing_table_is_silent(self):
        from health_insights.concern_rules import gap_rules

        db_path = _make_no_table_db()
        try:
            assert gap_rules.store_staleness_findings(db_path, _REF) == []
        finally:
            os.unlink(db_path)

    def test_registered_in_default_rules(self):
        from health_insights.concerns import DEFAULT_RULES

        assert "store_staleness" in [name for name, _ in DEFAULT_RULES]
