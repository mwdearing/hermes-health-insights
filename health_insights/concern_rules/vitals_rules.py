"""Resting heart rate, HRV and sleep concern rules.

Reuses ``anomalies._daily_values`` for daily aggregation; never
re-implements it.

Rules::

    rhr_elevated   (level 1) — 3 consecutive days >= baseline+8 bpm
    rhr_high_avg   (level 2) — 7-day mean above 100 bpm (suppresses rhr_elevated)
    hrv_low        (level 1) — 5 consecutive days below median-2*sigma
    sleep_very_low (level 2) — 7-day mean below 5.0 h
    sleep_low_avg  (level 1) — 7-day mean below 6.0 h OR >=1.5 h below baseline
    recovery_strain(level 2) — hrv_low AND rhr_elevated AND sleep < 6.0 h
                             (suppresses hrv_low and rhr_elevated)

Fewer than the required history days → no findings, no crash.
"""
from __future__ import annotations

from datetime import date as Date, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml

from .. import anomalies
from .. import stats as st
from health_insights import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHI = settings.timezone()


def _load_config() -> dict[str, Any]:
    """Return the health_monitor config dict."""
    _pkg = Path(__file__).resolve().parent.parent.parent
    _cfg_path = settings.package_data("health_monitor.yaml")
    with open(_cfg_path, "r") as fh:
        return yaml.safe_load(fh)


def _fmt_hours(h: float) -> str:
    """Format hours with one decimal, dropping trailing .0."""
    s = f"{h:.1f}"
    if s.endswith(".0"):
        return s[:-2]
    return s


def _fmt_bpm(v: float) -> str:
    """Format bpm as integer."""
    return str(round(v))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def vitals_findings(db_path: str, ref_date: Date) -> list["Finding"]:
    """Return vitals concern findings for the Chicago day *ref_date*."""
    from ..concerns import Finding  # late import to avoid circular import

    config = _load_config()
    chi = _CHI

    # --- Gather daily values ---
    # Wrap in try/except to handle missing tables gracefully
    try:
        rhr_daily = anomalies._daily_values(db_path, config, "resting_heart_rate", chi)
    except Exception:
        rhr_daily = {}
    try:
        hrv_daily = anomalies._daily_values(db_path, config, "heart_rate_variability_sdnn", chi)
    except Exception:
        hrv_daily = {}
    try:
        sleep_daily = anomalies._daily_values(db_path, config, "sleep_analysis", chi)
    except Exception:
        sleep_daily = {}

    out: list[Finding] = []

    # --- Resting HR ---
    rhr_elevated: Optional[Finding] = None
    rhr_high_avg: Optional[Finding] = None
    rhr_dates = sorted(rhr_daily)
    if len(rhr_dates) >= 14:
        baseline_dates = [d for d in rhr_dates
                          if (ref_date - timedelta(days=30)).isoformat() <= d <= (ref_date - timedelta(days=3)).isoformat()]
        baseline_vals = [rhr_daily[d] for d in baseline_dates]
        if len(baseline_vals) >= 14:
            baseline = st.median(baseline_vals)

            # rhr_high_avg: mean over ref-6..ref (at least 4 days) above 100
            high_start = (ref_date - timedelta(days=6)).isoformat()
            high_end = ref_date.isoformat()
            high_vals = [
                rhr_daily[d] for d in rhr_dates
                if high_start <= d <= high_end
            ]
            if len(high_vals) >= 4:
                avg = sum(high_vals) / len(high_vals)
                if avg > 100:
                    rhr_high_avg = Finding(
                        id="rhr_high_avg",
                        level=2,
                        title="Elevated 7-day average resting heart rate",
                        evidence=f"7-day average {_fmt_bpm(avg)} bpm at rest",
                        source="heuristic — 100 bpm resting tachycardia threshold",
                        advice=(
                            "Your resting heart rate average is high. "
                            "Worth discussing with your clinician."
                        ),
                    )

            if rhr_high_avg is None:
                # rhr_elevated: D >= baseline+8 on EVERY one of last 3 days (ref-2..ref)
                elev_start = (ref_date - timedelta(days=2)).isoformat()
                elev_end = ref_date.isoformat()
                elev_vals = [
                    rhr_daily[d] for d in rhr_dates
                    if elev_start <= d <= elev_end
                ]
                if len(elev_vals) == 3:
                    threshold = baseline + 8
                    if all(v >= threshold for v in elev_vals):
                        mean_val = sum(elev_vals) / 3
                        diff = round(mean_val - baseline)
                        rhr_elevated = Finding(
                            id="rhr_elevated",
                            level=1,
                            title="Elevated resting heart rate",
                            evidence=f"{diff} bpm above your 28-day normal for 3 days",
                            source="heuristic",
                            advice=(
                                "Worth watching — your resting heart rate has been "
                                "higher than usual for the last three days."
                            ),
                        )

    # --- HRV ---
    hrv_low: Optional[Finding] = None
    hrv_dates = sorted(hrv_daily)
    if len(hrv_dates) >= 14:
        baseline_dates = [d for d in hrv_dates
                          if (ref_date - timedelta(days=34)).isoformat() <= d <= (ref_date - timedelta(days=5)).isoformat()]
        baseline_vals = [hrv_daily[d] for d in baseline_dates]
        if len(baseline_vals) >= 14:
            baseline_median = st.median(baseline_vals)
            baseline_mad = st.mad(baseline_vals)
            if baseline_mad > 0:
                sigma = 1.4826 * baseline_mad
                threshold = baseline_median - 2 * sigma
                low_start = (ref_date - timedelta(days=4)).isoformat()
                low_end = ref_date.isoformat()
                low_vals = [
                    hrv_daily[d] for d in hrv_dates
                    if low_start <= d <= low_end
                ]
                if len(low_vals) == 5 and all(v < threshold for v in low_vals):
                    hrv_low = Finding(
                        id="hrv_low",
                        level=1,
                        title="Low heart rate variability",
                        evidence="HRV below your normal range for 5 days",
                        source="heuristic",
                        advice=(
                            "Worth watching — your heart rate variability has been "
                            "lower than usual for the last five days."
                        ),
                    )

    # --- Sleep ---
    sleep_low_avg: Optional[Finding] = None
    sleep_very_low: Optional[Finding] = None
    sleep_dates = sorted(sleep_daily)
    sleep_mean_7d: Optional[float] = None

    # 7-day mean (needed for recovery_strain check)
    sleep_start = (ref_date - timedelta(days=6)).isoformat()
    sleep_end = ref_date.isoformat()
    sleep_vals_7d = [
        sleep_daily[d] for d in sleep_dates
        if sleep_start <= d <= sleep_end
    ]
    if len(sleep_vals_7d) >= 4:
        sleep_mean_7d = sum(sleep_vals_7d) / len(sleep_vals_7d)

    if len(sleep_dates) >= 14:
        baseline_dates = [d for d in sleep_dates
                          if (ref_date - timedelta(days=34)).isoformat() <= d <= (ref_date - timedelta(days=7)).isoformat()]
        baseline_vals = [sleep_daily[d] for d in baseline_dates]
        baseline_median = st.median(baseline_vals) if len(baseline_vals) >= 14 else None

        # 7-day mean over ref-6..ref (at least 4 nights)
        if len(sleep_vals_7d) >= 4:
            mean_val = sum(sleep_vals_7d) / len(sleep_vals_7d)

            # sleep_very_low: mean < 5.0 h
            if mean_val < 5.0:
                sleep_very_low = Finding(
                    id="sleep_very_low",
                    level=2,
                    title="Very low sleep duration",
                    evidence=f"7-day average {_fmt_hours(mean_val)} h",
                    source="heuristic — 7+ h adult sleep guidance",
                    advice=(
                        "Your sleep has been very low. "
                        "Worth discussing with your clinician."
                    ),
                )
            else:
                # sleep_low_avg: mean < 6.0 h OR >=1.5 h below baseline
                rel_parts: list[str] = []
                if baseline_median is not None:
                    if mean_val <= baseline_median - 1.5:
                        rel_parts.append(f"vs {_fmt_hours(baseline_median)} h normal")
                if mean_val < 6.0 or rel_parts:
                    evidence = f"7-day average {_fmt_hours(mean_val)} h"
                    if rel_parts:
                        evidence += f"; {rel_parts[0]}"
                    sleep_low_avg = Finding(
                        id="sleep_low_avg",
                        level=1,
                        title="Low sleep duration",
                        evidence=evidence,
                        source="heuristic — 7+ h adult sleep guidance",
                        advice=(
                            "Worth watching — your sleep has been below your normal "
                            "range for the last seven days."
                        ),
                    )

    # --- Recovery strain ---
    recovery_strain: Optional[Finding] = None
    if hrv_low is not None and rhr_elevated is not None and sleep_mean_7d is not None and sleep_mean_7d < 6.0:
        recovery_strain = Finding(
            id="recovery_strain",
            level=2,
            title="Recovery strain detected",
            evidence=(
                f"Low HRV, elevated resting heart rate, and "
                f"reduced sleep ({_fmt_hours(sleep_mean_7d)} h avg)"
            ),
            source="heuristic",
            advice=(
                "possible illness or overload; discuss with your clinician "
                "if it continues"
            ),
        )

    # --- Assemble output (suppress individual findings when recovery_strain fires) ---
    if recovery_strain is not None:
        out.append(recovery_strain)
        # Sleep findings still fire
        if sleep_low_avg is not None:
            out.append(sleep_low_avg)
        if sleep_very_low is not None:
            out.append(sleep_very_low)
        # hrv_low and rhr_elevated suppressed
    else:
        if rhr_high_avg is not None:
            out.append(rhr_high_avg)
        if rhr_elevated is not None:
            out.append(rhr_elevated)
        if hrv_low is not None:
            out.append(hrv_low)
        if sleep_low_avg is not None:
            out.append(sleep_low_avg)
        if sleep_very_low is not None:
            out.append(sleep_very_low)

    # Sort by level desc, then id asc
    out.sort(key=lambda f: (-f.level, f.id))
    return out
