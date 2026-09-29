"""Tests for vitals concern rules (resting HR, HRV, sleep).

Synthetic data only — no real filesystem paths, no external data files.
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date, datetime, timedelta

import pytest

from health_insights.concern_rules.vitals_rules import vitals_findings
from health_insights.concerns import Finding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REF = date(2031, 5, 20)  # Chicago is CDT (UTC-5) all through May


def _build(rhr: dict[int, float] | None = None,
           hrv: dict[int, float] | None = None,
           sleep: dict[int, float] | None = None,
           days: int = 40) -> str:
    """Create a temp DB with samples + sleep_sessions.

    *days_before_REF* maps to the value for that day.
    RHR baseline: [56,57,58,57,56,58,57] → median 57, MAD 1
    HRV baseline: [50,52,54,52,50,54,52]  → median 52, MAD 2
    Sleep default: 7.5 h.
    """
    rhr = rhr or {}
    hrv = hrv or {}
    sleep = sleep or {}
    rhr_base = [56, 57, 58, 57, 56, 58, 57]
    hrv_base = [50, 52, 54, 52, 50, 54, 52]
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE samples ("
        "  sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, "
        "  client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "  value REAL, unit TEXT, metadata_json TEXT, "
        "  UNIQUE (type_code, start_time, value)"
        ")"
    )
    con.execute(
        "CREATE TABLE sleep_sessions ("
        "  sleep_session_id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, "
        "  client_record_id TEXT NOT NULL, start_time TEXT NOT NULL, "
        "  end_time TEXT NOT NULL, created_at TEXT, updated_at TEXT, "
        "  UNIQUE (source_id, client_record_id)"
        ")"
    )
    n = 0
    for back in range(days):
        d = _REF - timedelta(days=back)
        noon = datetime(d.year, d.month, d.day, 17, 0).strftime("%Y-%m-%dT%H:%M:%SZ")
        rr = rhr.get(back, rhr_base[back % 7])
        hh = hrv.get(back, hrv_base[back % 7])
        for tc, v, u in (("resting_heart_rate", rr, "bpm"),
                         ("heart_rate_variability_sdnn", hh, "ms")):
            n += 1
            con.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, "
                "start_time, end_time, value, unit) VALUES ('1',?,?,?,?,?,?)",
                (tc, f"r{n}", noon, noon, v, u),
            )
        hours = sleep.get(back, 7.5)
        end = datetime(d.year, d.month, d.day, 12, 0)
        start = end - timedelta(hours=hours)
        con.execute(
            "INSERT INTO sleep_sessions (source_id, client_record_id, "
            "start_time, end_time) VALUES (1,?,?,?)",
            (f"sl{back}", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
             end.strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
    con.commit()
    # Write to a temp file so the module can open it read-only.
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    dst = sqlite3.connect(tmp.name)
    con.backup(dst)
    con.close()
    dst.close()
    return tmp.name


def _last(n: int, v: float) -> dict[int, float]:
    """Return {0..n-1} → v (overrides for the last N days before REF)."""
    return {b: v for b in range(n)}


# ---------------------------------------------------------------------------
# Tests — one per behaviour
# ---------------------------------------------------------------------------

class TestRhrElevated:
    """rhr_elevated (level 1): D >= baseline+8 on EVERY one of last 3 days."""

    def test_rhr_elevated_fires_3_days(self):
        """Three consecutive days at 66 bpm (baseline 57 → threshold 65)."""
        db = _build(rhr=_last(3, 66))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_elevated" in ids

    def test_rhr_elevated_no_fire_2_days(self):
        """Only 2 high days — need 3."""
        db = _build(rhr=_last(2, 66))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_elevated" not in ids

    def test_rhr_elevated_evidence_format(self):
        """Evidence: 'X bpm above your Y-day normal for 3 days'."""
        db = _build(rhr=_last(3, 66))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "rhr_elevated")
        assert "9 bpm" in f.evidence
        assert "3 days" in f.evidence

    def test_rhr_elevated_not_fire_when_below_threshold(self):
        """Value at baseline+7 — one below threshold."""
        db = _build(rhr=_last(3, 64))  # 64 < 57+8=65
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_elevated" not in ids


class TestRhrHighAvg:
    """rhr_high_avg (level 2): mean over ref-6..ref (>=4 days) > 100."""

    def test_rhr_high_avg_fires(self):
        """7 days at 104 bpm → mean 104 > 100."""
        db = _build(rhr=_last(7, 104))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_high_avg" in ids

    def test_rhr_high_avg_suppresses_elevated(self):
        """When rhr_high_avg fires, rhr_elevated is NOT emitted."""
        db = _build(rhr=_last(7, 104))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_high_avg" in ids
        assert "rhr_elevated" not in ids

    def test_rhr_high_avg_evidence_format(self):
        """Evidence: '7-day average 104 bpm at rest'."""
        db = _build(rhr=_last(7, 104))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "rhr_high_avg")
        assert "104" in f.evidence

    def test_rhr_high_avg_advice_has_clinician(self):
        db = _build(rhr=_last(7, 104))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "rhr_high_avg")
        assert "clinician" in f.advice.lower()

    def test_rhr_high_avg_no_fire_below_100(self):
        """Mean at 99 bpm — below threshold."""
        db = _build(rhr=_last(7, 99))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_high_avg" not in ids


class TestHrvLow:
    """hrv_low (level 1): D < median-2*sigma on EVERY one of last 5 days."""

    def test_hrv_low_fires_5_days(self):
        """5 consecutive days at 44 ms (baseline median 52, sigma ~2.97 → threshold ~46)."""
        db = _build(hrv=_last(5, 44))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "hrv_low" in ids

    def test_hrv_low_no_fire_4_days(self):
        """Only 4 low days — need 5."""
        db = _build(hrv=_last(4, 44))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "hrv_low" not in ids

    def test_hrv_low_evidence_format(self):
        """Evidence contains '5 days'."""
        db = _build(hrv=_last(5, 44))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "hrv_low")
        assert "5 days" in f.evidence


class TestSleepVeryLow:
    """sleep_very_low (level 2): 7-day mean < 5.0 h."""

    def test_sleep_very_low_fires(self):
        """7 days at 4.5 h → mean 4.5 < 5.0."""
        db = _build(sleep=_last(7, 4.5))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "sleep_very_low" in ids

    def test_sleep_very_low_evidence_format(self):
        """Evidence: '7-day average 4.5 h'."""
        db = _build(sleep=_last(7, 4.5))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "sleep_very_low")
        assert "4.5 h" in f.evidence

    def test_sleep_very_low_advice_has_clinician(self):
        db = _build(sleep=_last(7, 4.5))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "sleep_very_low")
        assert "clinician" in f.advice.lower()

    def test_sleep_very_low_no_fire_at_5_0(self):
        """Exactly 5.0 h — not below 5.0."""
        db = _build(sleep=_last(7, 5.0))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "sleep_very_low" not in ids


class TestSleepLowAvg:
    """sleep_low_avg (level 1): 7-day mean < 6.0 h OR >= 1.5 h below baseline."""

    def test_sleep_low_avg_absolutefires(self):
        """7 days at 5.5 h → mean 5.5 < 6.0."""
        db = _build(sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "sleep_low_avg" in ids

    def test_sleep_low_avg_evidence_format(self):
        """Evidence: '7-day average 5.5 h'."""
        db = _build(sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "sleep_low_avg")
        assert "5.5 h" in f.evidence

    def test_sleep_low_avg_relative(self):
        """Baseline 8.0, last 7 at 6.4 → 1.6 below baseline (>=1.5)."""
        db = _build(sleep={**{b: 8.0 for b in range(40)}, **_last(7, 6.4)})
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "sleep_low_avg" in ids

    def test_sleep_low_avg_relative_evidence(self):
        """Evidence: '6.4 h' and '8 h' (no trailing .0)."""
        db = _build(sleep={**{b: 8.0 for b in range(40)}, **_last(7, 6.4)})
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "sleep_low_avg")
        assert "6.4 h" in f.evidence
        assert "8 h" in f.evidence

    def test_sleep_low_avg_no_fire_when_sufficient(self):
        """7 days at 7.5 h — normal."""
        db = _build(sleep=_last(7, 7.5))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "sleep_low_avg" not in ids
        assert "sleep_very_low" not in ids


class TestRecoveryStrain:
    """recovery_strain (level 2): hrv_low AND rhr_elevated AND sleep < 6.0."""

    def test_recovery_strain_fires(self):
        db = _build(rhr=_last(3, 66), hrv=_last(5, 44), sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "recovery_strain" in ids

    def test_recovery_strain_suppresses_hrv_low(self):
        db = _build(rhr=_last(3, 66), hrv=_last(5, 44), sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "hrv_low" not in ids

    def test_recovery_strain_suppresses_rhr_elevated(self):
        db = _build(rhr=_last(3, 66), hrv=_last(5, 44), sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "rhr_elevated" not in ids

    def test_recovery_strain_advice(self):
        db = _build(rhr=_last(3, 66), hrv=_last(5, 44), sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        f = next(f for f in findings if f.id == "recovery_strain")
        assert "illness" in f.advice.lower()
        assert "clinician" in f.advice.lower()


class TestMissingTables:
    """No tables or empty DB → no findings, no crash."""

    def test_empty_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        con = sqlite3.connect(tmp.name)
        con.close()
        findings = vitals_findings(tmp.name, _REF)
        assert findings == []

    def test_no_sleep_table(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        con = sqlite3.connect(tmp.name)
        con.execute(
            "CREATE TABLE samples ("
            "  sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, "
            "  client_record_id TEXT, start_time TEXT, end_time TEXT, "
            "  value REAL, unit TEXT, metadata_json TEXT"
            ")"
        )
        con.commit()
        con.close()
        findings = vitals_findings(tmp.name, _REF)
        assert findings == []


class TestThinHistory:
    """Fewer than 14 baseline days → no findings."""

    def test_thin_history_10_days(self):
        db = _build(days=10, rhr=_last(3, 70), hrv=_last(5, 40))
        findings = vitals_findings(db, _REF)
        assert findings == []


class TestSleepFewNights:
    """Fewer than 4 nights in the 7-day window → no sleep findings."""

    def test_sleep_few_nights_in_window(self):
        """Only 2 nights present in ref-6..ref (others missing)."""
        db = _build(days=40, sleep={b: 4.0 for b in range(3)})
        # Delete sleep sessions for ref-3..ref-6 to leave only ref-0 and ref-1
        con = sqlite3.connect(f"file:{db}?mode=rw", uri=True)
        for sl in ("sl3", "sl4", "sl5", "sl6"):
            con.execute("DELETE FROM sleep_sessions WHERE client_record_id = ?", (sl,))
        con.commit()
        con.close()
        findings = vitals_findings(db, _REF)
        ids = [f.id for f in findings]
        assert "sleep_low_avg" not in ids
        assert "sleep_very_low" not in ids


class TestSorting:
    """Findings sorted by level desc, then id asc."""

    def test_sorting_combined(self):
        db = _build(rhr=_last(3, 66), hrv=_last(5, 44), sleep=_last(7, 5.5))
        findings = vitals_findings(db, _REF)
        # recovery_strain (level 2) before sleep_low_avg (level 1)
        assert findings[0].level >= findings[-1].level
        if len(findings) >= 2:
            assert findings[0].id <= findings[1].id or findings[0].level > findings[1].level


class TestNormal:
    """No overrides → no findings."""

    def test_normal(self):
        db = _build()
        findings = vitals_findings(db, _REF)
        assert findings == []
