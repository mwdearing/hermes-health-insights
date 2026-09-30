"""Nutrition-gap concern rule.

Reuses ``nutrition.report_json()`` and ``nutrition._daily_sums()`` — never
re-implements the percent-of-target or "low" logic that already exists there.

Rules:
  nutrition_gap (level 1) — nutrients whose 7-day average is under 70 %
  of their DRI target AND have enough of their own tracking history
  (min_logged_days from dri.yaml rules section).
"""
from __future__ import annotations

from datetime import date as Date
from pathlib import Path

from .. import nutrition
from health_insights import settings


def nutrition_gap_findings(
    db_path: str,
    ref_date: Date,
    dri_path: str | None = None,
    days: int = 28,
) -> list["Finding"]:
    """Return nutrition-gap concern findings for the window ending at *ref_date*."""
    from ..concerns import Finding  # late import to avoid circular import

    # ref_date is a date OBJECT from concerns.evaluate(); report_json needs a string.
    ref_date_str = ref_date.isoformat() if hasattr(ref_date, "isoformat") else str(ref_date)

    # If dri_path is None, resolve the real config the same way vitals_rules does.
    if dri_path is None:
        _pkg = Path(__file__).resolve().parent.parent.parent
        dri_path = str(settings.package_data("dri.yaml"))

    # --- Step 1: call report_json ---
    try:
        rep = nutrition.report_json(db_path, dri_path, ref_date_str, days=days)
    except Exception:
        return []

    if "error" in rep or rep.get("days_logged", 0) == 0:
        return []

    # --- Load DRI config for labels and apple type codes ---
    dri_cfg = nutrition._load_dri(dri_path)
    rules_cfg = dri_cfg.get("rules", {})
    min_logged = rules_cfg.get("min_logged_days", 4)
    nutrients_cfg = dri_cfg.get("nutrients", {})

    # --- Step 2: compute per-nutrient own-day counts ---
    # _daily_sums returns {date_str: {type_code: sum_value}} for the window.
    ref = Date.fromisoformat(ref_date_str)
    start = ref - __import__("datetime").timedelta(days=days - 1)
    end = ref
    daily = nutrition._daily_sums(db_path, start, end)

    # Logged days = days with dietary_energy_consumed > 0
    logged_day_strings = [
        d for d, vals in daily.items() if vals.get("dietary_energy_consumed", 0) > 0
    ]

    # For each nutrient key, count own-days with a non-zero value.
    nutrient_own_days: dict[str, int] = {}
    for nut_key, nut_cfg in nutrients_cfg.items():
        apple = nut_cfg.get("apple")
        if not apple:
            continue
        count = sum(
            1
            for d in logged_day_strings
            if daily.get(d, {}).get(apple, 0) > 0
        )
        nutrient_own_days[nut_key] = count

    # --- Step 3: filter low nutrients by own-day gate ---
    candidates: list[tuple[str, int]] = []  # (label, pct)
    for nut_key, nut_data in rep.get("nutrients", {}).items():
        if nut_data.get("status") != "low":
            continue
        own = nutrient_own_days.get(nut_key, 0)
        if own < min_logged:
            continue
        # Pull label from dri config
        label = nutrients_cfg.get(nut_key, {}).get("label", nut_key)
        pct = nut_data.get("pct", 0)
        candidates.append((label, pct))

    if not candidates:
        return []

    # --- Step 4: build the single Finding ---
    # Sort lowest percent first
    candidates.sort(key=lambda x: x[1])
    evidence_parts = [f"{label} {pct}% of target" for label, pct in candidates]
    evidence = "; ".join(evidence_parts)

    n = len(candidates)
    if n == 1:
        nutrient_word = "one nutrient"
        pronoun = "its"
    else:
        nutrient_word = f"{n} nutrients"
        pronoun = "their"

    table_label = nutrition._dri_table("", dri_cfg)["label"]

    return [
        Finding(
            id="nutrition_gap",
            level=1,
            title="Some nutrients running low in your food log",
            evidence=evidence,
            source="heuristic (DRI comparison)",
            advice=(
                f"Your food log shows {nutrient_word} averaging below 70 % of "
                f"{pronoun} recommended amount (28-day average, compared with "
                f"the Dietary Reference Intakes for {table_label}). This isn't "
                f"an emergency, but if it persists it's worth raising with your "
                f"clinician or adjusting your diet."
            ),
        )
    ]
