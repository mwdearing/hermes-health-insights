"""Workout concern rules.

Reuses health_insights.workouts for _has_workouts_table, _open,
_workout_chicago_date, _parse_ts — never copies its logic.

Rules (priority order, AT MOST ONE finding):
  workout_load_spike   (level 1) — this week > 1.5× 4-week baseline avg
  no_recent_workouts   (level 1) — last workout > 14 days ago
  workout_consistency  (level 0) — at least one workout this week (positive)
"""
from __future__ import annotations

import sqlite3
from datetime import date as Date, timedelta

from ..workouts import _has_workouts_table, _open, _parse_ts, _workout_chicago_date


def workout_findings(db_path: str, ref_date) -> list["Finding"]:
    """Return at most ONE workout concern finding for the Chicago day *ref_date*.

    *ref_date* may be a date object or a "YYYY-MM-DD" string.
    A missing workouts table or no rows → return [], no crash.
    """
    from ..concerns import Finding  # late import to avoid circular import

    if not _has_workouts_table(db_path):
        return []

    ref = ref_date if isinstance(ref_date, Date) else Date.fromisoformat(ref_date)

    conn = _open(db_path)
    cur = conn.cursor()
    cur.execute("SELECT workout_type, end_time, duration_seconds FROM workouts")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return []

    # Bucket each workout by its Chicago end-date
    # Each row: (workout_type, end_time_str, duration_seconds)
    daily: dict[str, int] = {}  # "YYYY-MM-DD" → total seconds
    for _wtype, end_time, dur in rows:
        ch_date = _workout_chicago_date(end_time)
        daily[ch_date] = daily.get(ch_date, 0) + dur

    # "This week" = the 7 Chicago days ending on ref_date (inclusive)
    this_week_start = (ref - timedelta(days=6)).isoformat()
    this_week_end = ref.isoformat()
    this_week_seconds = sum(
        dur for d, dur in daily.items()
        if this_week_start <= d <= this_week_end
    )
    this_week_minutes = round(this_week_seconds / 60)
    # Session count for "this week": one per WORKOUT ROW, not one per distinct
    # day (2026-09-27 review: this used to count distinct days, so a day with
    # two separate workouts showed "1 workout" here while workouts.py's own
    # weekly_line -- shown right next to this on the card -- said "2 in 7d"
    # for the identical data. Match weekly_line's definition: a session count.
    this_week_sessions = sum(
        1 for _wtype, end_time, _dur in rows
        if this_week_start <= _workout_chicago_date(end_time) <= this_week_end
    )

    # Baseline: 4 complete 7-day windows before this week
    # ref-34 .. ref-7 (inclusive), in 4 chunks of 7 days
    baseline_seconds = 0
    baseline_weeks_with_data = 0
    for k in range(4):
        win_end = (ref - timedelta(days=7 + 7 * k)).isoformat()
        win_start = (ref - timedelta(days=13 + 7 * k)).isoformat()
        week_seconds = sum(
            dur for d, dur in daily.items()
            if win_start <= d <= win_end
        )
        baseline_seconds += week_seconds
        if week_seconds > 0:
            baseline_weeks_with_data += 1

    baseline_avg_minutes = round(baseline_seconds / 4 / 60)

    # A baseline built from only 0-1 active weeks out of 4 is too thin to call
    # this week's total a "spike" against (2026-09-27 review: a single lone
    # workout 20 days back must not make an ordinary week look like a spike).
    have_baseline = baseline_weeks_with_data >= 2

    # --- Priority 1: workout_load_spike (level 1) ---
    if have_baseline and baseline_avg_minutes > 0 and this_week_minutes > 1.5 * baseline_avg_minutes:
        return [
            Finding(
                id="workout_load_spike",
                level=1,
                title="Workout load spike detected",
                evidence=(
                    f"This week's total ({this_week_minutes} min) is well above "
                    f"your recent 4-week average ({baseline_avg_minutes} min/week)"
                ),
                source="heuristic — 1.5× baseline threshold",
                advice=(
                    "You've been more active this week than usual. "
                    "That's great — just ease into it to avoid injury."
                ),
            )
        ]

    # --- Priority 2: no_recent_workouts (level 1) ---
    # Find the most recent workout by Chicago end-date
    if daily:
        most_recent_date = max(daily.keys())
        days_since = (ref - Date.fromisoformat(most_recent_date)).days
        if days_since > 14:
            return [
                Finding(
                    id="no_recent_workouts",
                    level=1,
                    title="No recent workouts recorded",
                    evidence=f"Last recorded workout was {days_since} days ago (more than 14)",
                    source="heuristic — 14-day gap",
                    advice=(
                        "A couple of weeks without a recorded workout. "
                        "Even a short walk counts — get moving when you can."
                    ),
                )
            ]

    # --- Priority 3: workout_consistency (level 0 — positive) ---
    # At least one workout this week
    if this_week_seconds > 0:
        count = this_week_sessions
        s = "" if count == 1 else "s"
        return [
            Finding(
                id="workout_consistency",
                level=0,
                title="Workouts this week",
                evidence=f"{count} workout{s} this week, {this_week_minutes} min total",
                source="heuristic — positive signal",
                advice="Keep it up.",
            )
        ]

    # --- Priority 4: no finding ---
    # No workouts this week, no 14-day gap (a workout landed 7-13 days ago)
    return []
