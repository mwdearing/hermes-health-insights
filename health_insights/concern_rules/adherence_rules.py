"""Medication adherence concern rule.

Counts missed doses across all tracked medications over a trailing window by
shelling out to the ``medlog`` CLI (the source of truth for the medication
log; this module never reads or writes the log directly and never invents a
dose event). COUNTS ONLY: medication names and doses never appear in the
Finding text, only how many doses were missed and over what window, so the
output is safe to send to any channel. Opt-in: see settings.medlog_enabled().

No dosing or timing advice is ever given here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date as Date

from health_insights import settings

WINDOW_DAYS = 14
REPEAT_THRESHOLD = 2  # fewer than this is not "repeated"; never flag a single miss


def _missing_count(ref_date: Date, days: int) -> int | None:
    """Return the number of missed-dose entries in the trailing *days* window
    ending on *ref_date*, or None if `medlog` could not be run (never crash)."""
    medlog_bin = settings.medlog_bin()
    if shutil.which(medlog_bin) is None and not os.path.isfile(medlog_bin):
        return None
    env = dict(os.environ)
    # Pin "now" to the end of the reference day so a scheduled run and a
    # replay both see the same window, regardless of the wall clock.
    env.setdefault("MEDLOG_NOW", f"{ref_date.isoformat()}T23:59:00")
    try:
        result = subprocess.run(
            [medlog_bin, "--json", "missing", "--days", str(days)],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    return len(rows)


def adherence_findings(db_path: str, ref_date, days: int = WINDOW_DAYS) -> list["Finding"]:
    """Return a medication-adherence Finding when doses have been repeatedly missed.
    Returns [] unless the medlog integration is enabled (settings.medlog_enabled()).

    *db_path* is accepted (and ignored) only so this rule has the same
    signature as every other rule in DEFAULT_RULES; adherence data lives in
    the medlog store, not the merged history database. *ref_date* may be a
    date object or a "YYYY-MM-DD" string.
    """
    if not settings.medlog_enabled():
        return []
    from ..concerns import Finding  # late import to avoid circular import

    ref = Date.fromisoformat(ref_date) if isinstance(ref_date, str) else ref_date
    count = _missing_count(ref, days)
    if count is None or count < REPEAT_THRESHOLD:
        return []
    return [
        Finding(
            id="medication_adherence",
            level=1,
            title="Repeated missed medication doses",
            evidence=f"{count} missed doses recorded in the last {days} days across your tracked medications",
            source="medlog (counts only)",
            advice=(
                "No dosing advice here — just a nudge to check your medication log "
                "if this is more misses than you'd expect."
            ),
        )
    ]
