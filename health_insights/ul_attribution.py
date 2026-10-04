"""UL (Tolerable Upper Intake Level) attribution by form and source.

Reports factual UL comparisons per nutrient. Statuses only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable


@dataclass(frozen=True)
class Contribution:
    """One intake amount of one nutrient on one local day."""
    day: str
    nutrient: str
    amount: Decimal | None
    unit: str
    source: str  # "food" | "supplement" | "unknown"
    form: str | None = None


@dataclass
class ULFinding:
    """Result of comparing contributions against a UL threshold."""
    nutrient: str
    ul: Decimal | None
    unit: str
    ul_scope: str
    ul_form: str
    status: str  # "above" | "within" | "undetermined" | "no_ul"
    high_days: list[str] = field(default_factory=list)  # sorted
    undetermined_days: list[str] = field(default_factory=list)  # sorted
    mean_daily: Decimal | None = None
    days_counted: int = 0


def _day_total(
    contributions: list[Contribution],
    nutrient: str,
    dri: dict,
) -> tuple[int, Decimal | None, list[str], list[str]]:
    """Compute determined-day total, mean, high days, undetermined days.

    Returns (days_counted, mean_daily, high_days, undetermined_days).
    """
    ul = dri["ul"]
    ul_scope = dri["ul_scope"]
    ul_form = dri["form"]
    ul_unit = dri["unit"]

    # Group contributions by day for this nutrient
    days: dict[str, list[Contribution]] = {}
    for c in contributions:
        if c.nutrient != nutrient:
            continue
        days.setdefault(c.day, []).append(c)

    high_days: list[str] = []
    undetermined_days: list[str] = []
    determined_totals: list[Decimal] = []

    for day_key in sorted(days):
        day_contribs = days[day_key]
        day_total = Decimal("0")
        day_is_determined = True
        day_has_counted = False

        for c in day_contribs:
            # Rule: supplements-only scope -> food never counts
            if ul_scope == "supplements" and c.source == "food":
                continue

            # Rule: supplements-only scope -> unknown source makes day undetermined
            if ul_scope == "supplements" and c.source == "unknown":
                day_is_determined = False
                continue

            # Rule: form-specific UL -> case-insensitive match
            if ul_form != "any":
                if c.form is None:
                    day_is_determined = False
                    continue
                if c.form.lower() != ul_form.lower():
                    # Different form: simply does not count
                    continue

            # Rule: amount None -> undetermined
            if c.amount is None:
                day_is_determined = False
                continue

            # Rule: unit mismatch -> undetermined (never converted)
            if c.unit != ul_unit:
                day_is_determined = False
                continue

            # Counted contribution
            day_total += c.amount
            day_has_counted = True

        if not day_is_determined:
            undetermined_days.append(day_key)
        elif day_has_counted:
            determined_totals.append(day_total)
            if ul is not None and day_total > ul:
                high_days.append(day_key)
        # else: day has contributions but none matched the form — not counted

    if determined_totals:
        mean_daily = sum(determined_totals) / Decimal(str(len(determined_totals)))
    else:
        mean_daily = None

    return len(determined_totals), mean_daily, high_days, undetermined_days


def compare_to_ul(
    contributions: list[Contribution],
    dri_nutrients: dict[str, dict],
) -> list[ULFinding]:
    """Compare nutrient intake contributions against Tolerable Upper Intake Levels.

    Returns one ULFinding per nutrient that has contributions, sorted by
    nutrient name. Nutrients with no contributions are omitted.
    """
    # Collect which nutrients have contributions
    nutrient_contribs: dict[str, list[Contribution]] = {}
    for c in contributions:
        nutrient_contribs.setdefault(c.nutrient, []).append(c)

    results: list[ULFinding] = []
    for nutrient in sorted(nutrient_contribs):
        if nutrient not in dri_nutrients:
            continue

        dri = dri_nutrients[nutrient]
        ul = dri["ul"]
        ul_scope = dri["ul_scope"]
        ul_form = dri["form"]
        ul_unit = dri["unit"]

        days_counted, mean_daily, high_days, undetermined_days = _day_total(
            contributions, nutrient, dri
        )

        if ul is None:
            status = "no_ul"
            mean_daily = None
            days_counted = 0
        else:
            if high_days:
                status = "above"
            elif undetermined_days:
                status = "undetermined"
            else:
                status = "within"

        results.append(ULFinding(
            nutrient=nutrient,
            ul=ul,
            unit=ul_unit,
            ul_scope=ul_scope,
            ul_form=ul_form,
            status=status,
            high_days=sorted(high_days),
            undetermined_days=sorted(undetermined_days),
            mean_daily=mean_daily,
            days_counted=days_counted,
        ))

    return results
