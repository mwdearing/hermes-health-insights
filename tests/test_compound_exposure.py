"""Tests for compound_exposure_summary and the compounds CLI subcommand."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from health_insights.evidence import CompoundExposure, compound_exposure_summary
from health_insights.intake_reader import IntakeComponent


def _comp(
    kind: str = "compound",
    code: str = "creatine",
    amount: Optional[str] = "3",
    unit: Optional[str] = "g",
    value_state: str = "known",
    occurred_at: str = "2026-10-01T07:00:00-05:00",
    time_zone: str = "America/Chicago",
    position: int = 0,
    intake_id: str = "intake-a",
    producer_id: str = "nutrition-app",
    quantity_basis: Optional[str] = "compound_mass",
    label_name: Optional[str] = None,
) -> IntakeComponent:
    return IntakeComponent(
        owner_id="owner-1",
        producer_id=producer_id,
        intake_id=intake_id,
        revision=1,
        occurred_at=occurred_at,
        time_zone=time_zone,
        category="drink",
        display_name="Test",
        component_id="c1",
        position=position,
        kind=kind,
        code=code,
        value_state=value_state,
        amount=amount,
        unit=unit,
        quantity_basis=quantity_basis,
        aggregation_role="compound_measurement" if kind == "compound" else "context_only",
        provenance="user_confirmed",
        label_name=label_name,
    )


# ---------------------------------------------------------------------------
# Test 1: compound_exposure_summary returns one row per (code, form, unit)
# ---------------------------------------------------------------------------

_MINI_SCHEMA = """
create table intake_revisions (
    intake_revision_row_id integer primary key, owner_id text, producer_id text, intake_id text,
    revision integer, occurred_at text, time_zone text, category text, display_name text);
create table intake_compound_facts (
    intake_revision_row_id integer, component_id text, position integer, kind text, code text,
    value_state text, amount text, unit text, quantity_basis text, aggregation_role text,
    provenance text, label_name text);
create table intake_state (owner_id text, producer_id text, intake_id text, deleted integer);
create table intake_tombstones (owner_id text, producer_id text, intake_id text);
"""


def _mini_intake_db() -> Path:
    """A minimal synthetic receiver database with one creatine component (3 g)."""
    db_path = Path(tempfile.mkdtemp(prefix="compound-cli-")) / "test.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_MINI_SCHEMA)
        conn.execute(
            "insert into intake_revisions values (1, 'owner-1', 'nutrition-app', 'intake-a', 1,"
            " '2026-10-01T07:00:00-05:00', 'America/Chicago', 'supplement', 'Synthetic Powder')"
        )
        conn.execute(
            "insert into intake_compound_facts values (1, 'c1', 0, 'compound', 'creatine', 'known',"
            " '3', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', null)"
        )
    return db_path

class TestExposureSummaryBasic:
    """Basic grouping: one creatine/g row with correct totals."""

    def test_one_row_per_compound(self):
        comps = [
            _comp(amount="3"),
            _comp(amount="2.5", intake_id="intake-b", producer_id="other-app"),
        ]
        rows = compound_exposure_summary(comps)
        assert len(rows) == 1
        r = rows[0]
        assert r.substance == "creatine"
        assert r.unit == "g"
        assert r.total == Decimal("5.5")
        assert r.known_count == 2
        assert r.unknown_count == 0


# ---------------------------------------------------------------------------
# Test 2: unknown amounts counted, not zeroed
# ---------------------------------------------------------------------------
class TestExposureUnknown:
    """Unknown amounts contribute to unknown_count, never as zero total."""

    def test_unknown_not_zeroed(self):
        comps = [_comp(amount=None, value_state="unknown")]
        rows = compound_exposure_summary(comps)
        assert len(rows) == 1
        assert rows[0].total is None
        assert rows[0].unknown_count == 1
        assert rows[0].known_count == 0

    def test_mixed_known_unknown(self):
        comps = [
            _comp(amount="3"),
            _comp(amount=None, value_state="unknown", intake_id="intake-b"),
        ]
        rows = compound_exposure_summary(comps)
        assert rows[0].total == Decimal("3")
        assert rows[0].known_count == 1
        assert rows[0].unknown_count == 1


# ---------------------------------------------------------------------------
# Test 3: nutrients and blends excluded
# ---------------------------------------------------------------------------
class TestExposureExcludesNonCompound:
    """Only compound kind appears in the summary."""

    def test_nutrient_not_in_summary(self):
        comps = [_comp(kind="nutrient", code="vit_c", amount="100", unit="mg")]
        rows = compound_exposure_summary(comps)
        assert len(rows) == 0

    def test_blend_not_in_summary(self):
        comps = [_comp(kind="blend", code="blend-x", amount="10", unit="g")]
        rows = compound_exposure_summary(comps)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Test 4: sources list from producer_id
# ---------------------------------------------------------------------------
class TestExposureSources:
    """Sources = sorted distinct producer_id of counted components."""

    def test_sources_from_multiple_producers(self):
        comps = [
            _comp(amount="3"),
            _comp(amount="2", intake_id="intake-b", producer_id="other-app"),
            _comp(amount="1", intake_id="intake-c", producer_id="nutrition-app"),
        ]
        rows = compound_exposure_summary(comps)
        assert sorted(rows[0].sources) == ["nutrition-app", "other-app"]


# ---------------------------------------------------------------------------
# Test 5: date window filter by local day
# ---------------------------------------------------------------------------
class TestExposureDateWindow:
    """start/end filters by local day (inclusive)."""

    def test_window_filters_days(self):
        comps = [
            _comp(amount="1", occurred_at="2026-09-30T07:00:00-05:00", time_zone="America/Chicago", intake_id="intake-a"),
            _comp(amount="2", occurred_at="2026-10-01T07:00:00-05:00", time_zone="America/Chicago", intake_id="intake-b"),
            _comp(amount="3", occurred_at="2026-10-02T07:00:00-05:00", time_zone="America/Chicago", intake_id="intake-c"),
        ]
        rows = compound_exposure_summary(comps, start="2026-10-01", end="2026-10-01")
        assert len(rows) == 1
        assert rows[0].total == Decimal("2")
        assert rows[0].first_day == "2026-10-01"
        assert rows[0].last_day == "2026-10-01"
        assert rows[0].days == 1

    def test_window_excludes_all(self):
        comps = [
            _comp(amount="1", occurred_at="2026-09-30T07:00:00-05:00", time_zone="America/Chicago", intake_id="intake-a"),
        ]
        rows = compound_exposure_summary(comps, start="2026-10-01", end="2026-10-01")
        assert rows == []


# ---------------------------------------------------------------------------
# Test 6: first_day, last_day, days
# ---------------------------------------------------------------------------
class TestExposureDateRange:
    """first_day, last_day, and days are correct."""

    def test_date_range(self):
        comps = [
            _comp(amount="1", occurred_at="2026-10-01T07:00:00-05:00", time_zone="America/Chicago", intake_id="intake-a"),
            _comp(amount="2", occurred_at="2026-10-03T07:00:00-05:00", time_zone="America/Chicago", intake_id="intake-b"),
        ]
        rows = compound_exposure_summary(comps)
        assert rows[0].first_day == "2026-10-01"
        assert rows[0].last_day == "2026-10-03"
        assert rows[0].days == 2


# ---------------------------------------------------------------------------
# Test 7: form uses label_name or quantity_basis
# ---------------------------------------------------------------------------
class TestExposureForm:
    """form = label_name if present, else quantity_basis."""

    def test_form_from_label_name(self):
        comps = [_comp(amount="3", label_name="Creatine Monohydrate")]
        rows = compound_exposure_summary(comps)
        assert rows[0].form == "Creatine Monohydrate"

    def test_form_from_quantity_basis(self):
        comps = [_comp(amount="3", quantity_basis="compound_mass", label_name=None)]
        rows = compound_exposure_summary(comps)
        assert rows[0].form == "compound_mass"

    def test_form_none_when_missing(self):
        comps = [_comp(amount="3", quantity_basis=None, label_name=None)]
        rows = compound_exposure_summary(comps)
        assert rows[0].form is None


# ---------------------------------------------------------------------------
# Test 8: empty input
# ---------------------------------------------------------------------------
class TestExposureEmpty:
    """Empty input returns empty list."""

    def test_no_components(self):
        rows = compound_exposure_summary([])
        assert rows == []


# ---------------------------------------------------------------------------
# Test 9: CLI JSON output shape
# ---------------------------------------------------------------------------
class TestCLIJsonShape:
    """CLI compounds --json produces correct JSON structure."""

    def test_json_has_compounds_key(self):
        # Build a minimal synthetic DB using the health-relay-review test helpers
        db_path = _mini_intake_db()

        result = subprocess.run(
            [sys.executable, "-m", "health_insights.cli", "compounds", "--db", str(db_path), "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"compounds --json exited {result.returncode}: {result.stderr[:200]}"
        doc = json.loads(result.stdout)
        assert "compounds" in doc
        assert isinstance(doc["compounds"], list)
        if doc["compounds"]:
            row = doc["compounds"][0]
            assert "substance" in row
            assert "form" in row
            assert "unit" in row
            assert "total" in row
            assert "known_count" in row
            assert "unknown_count" in row
            assert "first_day" in row
            assert "last_day" in row
            assert "days" in row
            assert "sources" in row


# ---------------------------------------------------------------------------
# Test 10: CLI text output
# ---------------------------------------------------------------------------
class TestCLITextOutput:
    """CLI compounds (no --json) produces a text table with compound names."""

    def test_text_lists_compound(self):
        db_path = _mini_intake_db()

        result = subprocess.run(
            [sys.executable, "-m", "health_insights.cli", "compounds", "--db", str(db_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "creatine" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 11: CLI empty DB
# ---------------------------------------------------------------------------
class TestCLIEmptyDB:
    """DB without intake tables returns empty list and exit 0."""

    def test_no_intake_tables(self):
        import sqlite3
        scratch = tempfile.mkdtemp(prefix="nd05-cli-")
        db_path = Path(scratch) / "empty.sqlite"
        sqlite3.connect(db_path).execute("create table samples (id integer primary key)").connection.commit()

        result = subprocess.run(
            [sys.executable, "-m", "health_insights.cli", "compounds", "--db", str(db_path), "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        doc = json.loads(result.stdout or "{}")
        assert doc.get("compounds") == []
