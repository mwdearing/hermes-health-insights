"""Per-day compound totals from effective intake components.

Only components with kind == "compound" contribute to totals.  Amounts are
summed as ``Decimal`` so there is no float drift.  Components whose
value_state is not "known" or whose amount is None are counted in
unknown_count (never as zero).  Nutrient and blend kinds are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from zoneinfo import ZoneInfo

from health_insights.intake_reader import IntakeComponent


@dataclass
class CompoundExposure:
    """One-row summary for a single compound (substance + form + unit)."""

    substance: str
    form: Optional[str]
    unit: Optional[str]
    total: Optional[Decimal]
    known_count: int
    unknown_count: int
    first_day: str
    last_day: str
    days: int
    sources: list[str]


@dataclass
class CompoundDay:
    day: str
    code: str
    unit: Optional[str]
    total: Optional[Decimal]
    known_count: int
    unknown_count: int


def compound_totals_by_day(
    components: list[IntakeComponent],
) -> list[CompoundDay]:
    """Group compound components by local date and sum known amounts.

    The local date is derived from ``occurred_at`` using the component's own
    ``time_zone`` (via ``zoneinfo.ZoneInfo``), not UTC.
    """

    # (day, code, unit) -> { "total": Decimal, "known": int, "unknown": int }
    buckets: dict[tuple[str, str, Optional[str]], dict] = {}

    for comp in components:
        if comp.kind != "compound":
            continue

        # Compute local date from occurred_at in the component's timezone
        tz = ZoneInfo(comp.time_zone)
        s = comp.occurred_at.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        local_date = dt.astimezone(tz).strftime("%Y-%m-%d")

        key = (local_date, comp.code, comp.unit)
        if key not in buckets:
            buckets[key] = {"total": Decimal("0"), "known": 0, "unknown": 0}

        bucket = buckets[key]

        if comp.value_state == "known" and comp.amount is not None:
            bucket["total"] += Decimal(comp.amount)
            bucket["known"] += 1
        else:
            bucket["unknown"] += 1

    result: list[CompoundDay] = []
    for (day, code, unit), b in buckets.items():
        total = b["total"] if b["known"] > 0 else None
        result.append(
            CompoundDay(
                day=day,
                code=code,
                unit=unit,
                total=total,
                known_count=b["known"],
                unknown_count=b["unknown"],
            )
        )

    return result


def compound_exposure_summary(
    components: list[IntakeComponent],
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[CompoundExposure]:
    """Aggregate compound exposure across all days.

    Returns one ``CompoundExposure`` row per (code, form, unit) group.
    Only ``kind == "compound"`` contributes.  Nutrients and blends are
    skipped.  Unknown amounts are counted in ``unknown_count`` only.

    ``start`` / ``end`` filter by local day (inclusive).  When both are
    omitted all days are included.
    """

    # (code, form, unit) -> { "total": Decimal, "known": int, "unknown": int,
    #                         "days": set[str], "sources": set[str] }
    buckets: dict[tuple[str, Optional[str], Optional[str]], dict] = {}

    for comp in components:
        if comp.kind != "compound":
            continue

        # Compute local date
        tz = ZoneInfo(comp.time_zone)
        s = comp.occurred_at.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        local_date = dt.astimezone(tz).strftime("%Y-%m-%d")

        # Apply local-day window filter
        if start and local_date < start:
            continue
        if end and local_date > end:
            continue

        # form: label_name preferred, fallback to quantity_basis
        form = comp.label_name if comp.label_name else comp.quantity_basis

        key = (comp.code, form, comp.unit)
        if key not in buckets:
            buckets[key] = {
                "total": Decimal("0"),
                "known": 0,
                "unknown": 0,
                "days": set[str](),
                "sources": set[str](),
            }

        bucket = buckets[key]
        bucket["days"].add(local_date)
        bucket["sources"].add(comp.producer_id)

        if comp.value_state == "known" and comp.amount is not None:
            bucket["total"] += Decimal(comp.amount)
            bucket["known"] += 1
        else:
            bucket["unknown"] += 1

    result: list[CompoundExposure] = []
    for (code, form, unit), b in buckets.items():
        total = b["total"] if b["known"] > 0 else None
        sorted_days = sorted(b["days"])
        n_days = len(sorted_days)
        if n_days == 0:
            n_days = 1  # single entry
        result.append(
            CompoundExposure(
                substance=code,
                form=form,
                unit=unit,
                total=total,
                known_count=b["known"],
                unknown_count=b["unknown"],
                first_day=sorted_days[0] if sorted_days else "",
                last_day=sorted_days[-1] if sorted_days else "",
                days=n_days,
                sources=sorted(b["sources"]),
            )
        )

    return sorted(result, key=lambda r: (r.substance, r.form or "", r.unit or ""))
