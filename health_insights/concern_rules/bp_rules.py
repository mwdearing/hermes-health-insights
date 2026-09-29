"""Blood-pressure concern rules.

Reuses health_insights.bp for _readings, _seven_day_avg,
category, and _round_half_up — never copies its logic.

Rules:
  bp_severe   (level 3) — any reading in the two Chicago days
              ref-1 .. ref is category Severe.
  bp_stage2_avg (level 2) — 7-day average is Stage 2.
  bp_stage1_avg (level 1) — 7-day average is Stage 1 AND the
              previous window (ref-7) average is Stage 1 or Stage 2.

Fewer than 3 readings, no readings, or no samples table → no
findings, no crash (sqlite3.OperationalError caught around the
read only).
"""
from __future__ import annotations

import sqlite3
from datetime import date as Date, timedelta

from .. import bp


def bp_findings(db_path: str, ref_date: Date) -> list["Finding"]:
    """Return BP concern findings for the Chicago day *ref_date*."""
    from ..concerns import Finding  # late import to avoid circular import

    try:
        readings = bp._readings(db_path)
    except sqlite3.OperationalError:
        return []

    out: list[Finding] = []

    # --- bp_severe (level 3) ---
    # Two-day window: ref-1 .. ref (inclusive)
    severe_start = (ref_date - timedelta(days=1)).isoformat()
    severe_readings = [
        r for r in readings
        if severe_start <= r[0] <= ref_date.isoformat()
        and bp.category(r[2], r[3]) == "Severe"
    ]
    if severe_readings:
        # Report the worst reading in the window
        worst = max(severe_readings, key=lambda r: (r[2], r[3]))
        sys_v = bp._round_half_up(worst[2])
        dia_v = bp._round_half_up(worst[3])
        day_str = f"{Date.fromisoformat(worst[0]):%Y-%m-%d}"
        out.append(
            Finding(
                id="bp_severe",
                level=3,
                title="Severe blood pressure reading",
                evidence=f"Reading {sys_v}/{dia_v} on {day_str}",
                source="AHA (American Heart Association) — crisis threshold",
                advice=(
                    "Sit quietly and recheck after 5 minutes. "
                    "If confirmed, or if you have chest pain, breathlessness, "
                    "weakness, vision or speech changes, seek care now."
                ),
            )
        )

    # --- 7-day average checks ---
    seven = bp._seven_day_avg(readings, ref_date)
    if seven is None:
        return out

    cat, count, _ = seven

    if cat == "Stage 2":
        sys_avg = bp._round_half_up(sum(r[2] for r in readings
                    if (ref_date - timedelta(days=6)).isoformat() <= r[0] <= ref_date.isoformat()) / count)
        dia_avg = bp._round_half_up(sum(r[3] for r in readings
                    if (ref_date - timedelta(days=6)).isoformat() <= r[0] <= ref_date.isoformat()) / count)
        out.append(
            Finding(
                id="bp_stage2_avg",
                level=2,
                title="7-day average in Stage 2 range",
                evidence=f"7-day average {sys_avg}/{dia_avg} from {count} readings",
                source="AHA — Stage 2 hypertension",
                advice=(
                    "Your blood pressure average is elevated. "
                    "Worth discussing with your clinician at the next visit."
                ),
            )
        )
        return out

    if cat == "Stage 1":
        # Check prior window (ref-7 .. ref-14)
        prior_start = (ref_date - timedelta(days=14)).isoformat()
        prior_end = (ref_date - timedelta(days=7)).isoformat()
        prior_readings = [
            r for r in readings
            if prior_start <= r[0] <= prior_end
        ]
        prior_count = len(prior_readings)
        if prior_count >= 3:
            prior_sys = sum(r[2] for r in prior_readings) / prior_count
            prior_dia = sum(r[3] for r in prior_readings) / prior_count
            prior_cat = bp.category(prior_sys, prior_dia)
            if prior_cat in ("Stage 1", "Stage 2"):
                sys_avg = bp._round_half_up(sum(r[2] for r in readings
                            if (ref_date - timedelta(days=6)).isoformat() <= r[0] <= ref_date.isoformat()) / count)
                dia_avg = bp._round_half_up(sum(r[3] for r in readings
                            if (ref_date - timedelta(days=6)).isoformat() <= r[0] <= ref_date.isoformat()) / count)
                out.append(
                    Finding(
                        id="bp_stage1_avg",
                        level=1,
                        title="7-day average in Stage 1 range",
                        evidence=f"7-day average {sys_avg}/{dia_avg} from {count} readings",
                        source="AHA — Stage 1 hypertension",
                        advice=(
                            "Your blood pressure average is mildly elevated "
                            "and has persisted. Worth monitoring and discussing "
                            "with your clinician if it continues."
                        ),
                    )
                )

    return out
