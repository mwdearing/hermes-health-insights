"""Tests for health_insights.evidence — compound_totals_by_day."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import pytest

from health_insights.evidence import CompoundDay, compound_totals_by_day
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
        quantity_basis="compound_mass" if kind == "compound" else None,
        aggregation_role="compound_measurement" if kind == "compound" else "context_only",
        provenance="user_confirmed",
        label_name=None,
    )


class TestCompoundTotalDecimal:
    """Known amounts sum as Decimal — no float drift."""

    def test_decimal_sum(self):
        comps = [
            _comp(amount="2.5"),
            _comp(amount="5", intake_id="intake-b", producer_id="other-app"),
        ]
        days = compound_totals_by_day(comps)
        assert len(days) == 1
        assert days[0].total == Decimal("7.5")
        assert days[0].known_count == 2
        assert isinstance(days[0].total, Decimal)

    def test_float_drift_absent(self):
        """2.1 + 0.2 must not be 2.3000000000000003."""
        comps = [
            _comp(amount="2.1"),
            _comp(amount="0.2", intake_id="intake-b"),
        ]
        days = compound_totals_by_day(comps)
        assert days[0].total == Decimal("2.3")


class TestUnknownAsUnknown:
    """Unknown amounts count in unknown_count, never as zero."""

    def test_unknown_not_zero(self):
        comps = [_comp(amount=None, value_state="unknown")]
        days = compound_totals_by_day(comps)
        assert len(days) == 1
        assert days[0].total is None
        assert days[0].unknown_count == 1
        assert days[0].known_count == 0

    def test_mixed_known_unknown(self):
        comps = [
            _comp(amount="3"),
            _comp(amount=None, value_state="unknown", intake_id="intake-b"),
        ]
        days = compound_totals_by_day(comps)
        assert days[0].total == Decimal("3")
        assert days[0].known_count == 1
        assert days[0].unknown_count == 1


class TestNutrientSkipped:
    """Nutrient kind must never be totalled."""

    def test_nutrient_not_in_totals(self):
        comps = [_comp(kind="nutrient", code="dietary_energy_consumed", amount="100", unit="kcal")]
        days = compound_totals_by_day(comps)
        assert len(days) == 0

    def test_blend_not_in_totals(self):
        comps = [_comp(kind="blend", code="blend-x", amount="10", unit="g")]
        days = compound_totals_by_day(comps)
        assert len(days) == 0


class TestLocalDayTimezone:
    """Local date uses the component's own time_zone, not UTC."""

    def test_late_evening_stays_same_day(self):
        """23:30 at -05:00 should be 2026-10-02, not 2026-10-03 (UTC)."""
        comps = [_comp(
            occurred_at="2026-10-02T23:30:00-05:00",
            time_zone="America/Chicago",
            amount="3",
        )]
        days = compound_totals_by_day(comps)
        assert len(days) == 1
        assert days[0].day == "2026-10-02"

    def test_different_timezone_different_day(self):
        """Different local dates when same UTC instant falls on different days."""
        comps = [
            _comp(occurred_at="2026-10-02T03:00:00-05:00", time_zone="America/Chicago", amount="1"),
            _comp(occurred_at="2026-10-01T03:00:00-07:00", time_zone="America/Denver", amount="2", intake_id="intake-b"),
        ]
        days = compound_totals_by_day(comps)
        assert len(days) == 2
        days_by_day = {d.day: d for d in days}
        assert days_by_day["2026-10-02"].total == Decimal("1")
        assert days_by_day["2026-10-01"].total == Decimal("2")


class TestEmptyInput:
    """Empty input returns empty output."""

    def test_no_components(self):
        days = compound_totals_by_day([])
        assert days == []
