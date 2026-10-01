"""Lab out-of-range concern rule.

Reads the lab_results table from the merged history DB and reports any
result with a LOW or HIGH flag within the 90-day window ending on
*ref_date*.

Rules:
  lab_out_of_range (level 2) — any result outside the lab's own
  reference range in the 90-day window.

No findings → return []. Missing table or file → no crash, no findings.
"""
from __future__ import annotations

import sqlite3
from datetime import date as Date, timedelta
from health_insights.sqlite_ro import connect_readonly


def labs_findings(db_path: str, ref_date) -> list["Finding"]:
    """Return lab out-of-range findings for the day *ref_date*.

    Parameters
    ----------
    db_path : str
        Path to the merged history SQLite database.
    ref_date : date or str
        Reference date (inclusive end of the 90-day window).

    Returns
    -------
    list[Finding]
        At most one Finding with id="lab_out_of_range" (level 2).
    """
    from ..concerns import Finding  # late import to avoid circular import

    if isinstance(ref_date, str):
        ref_date = Date.fromisoformat(ref_date)

    try:
        con = connect_readonly(db_path)
    except sqlite3.OperationalError:
        return []

    try:
        rows = con.execute(
            "SELECT name, effective_date, value_num, unit, ref_low, ref_high, flag "
            "FROM lab_results"
        ).fetchall()
    except sqlite3.OperationalError:
        con.close()
        return []

    window_start = (ref_date - timedelta(days=90)).isoformat()
    ref_str = ref_date.isoformat()

    qualifying: list[tuple] = []
    for name, eff_date, value_num, unit, ref_low, ref_high, flag in rows:
        if flag not in ("LOW", "HIGH"):
            continue
        if eff_date < window_start or eff_date > ref_str:
            continue
        qualifying.append((name, eff_date, value_num, unit, ref_low, ref_high, flag))

    con.close()

    if not qualifying:
        return []

    # Sort oldest first for evidence ordering
    qualifying.sort(key=lambda r: r[1])

    count = len(qualifying)
    result_word = "result" if count == 1 else "results"
    evidence_parts = [f"{count} {result_word} outside the lab's own reference range"]

    for name, eff_date, value_num, unit, ref_low, ref_high, flag in qualifying:
        direction = flag.lower()
        # Build reference range string
        if ref_low is not None and ref_high is not None:
            range_str = f"{ref_low}-{ref_high}"
        elif ref_low is not None:
            range_str = f">{ref_low}"
        elif ref_high is not None:
            range_str = f"<{ref_high}"
        else:
            range_str = "no reference range"

        val_str = str(value_num) if value_num is not None else "N/A"
        unit_str = unit if unit else ""
        unit_part = f" {unit_str}" if unit_str else ""
        detail = f"{name} {val_str}{unit_part} ({direction}, reference range {range_str}{unit_part})"
        evidence_parts.append(detail)

    return [
        Finding(
            id="lab_out_of_range",
            level=2,
            title="New lab result outside its reference range",
            evidence="; ".join(evidence_parts),
            source="the lab's own reference range",
            advice=(
                "A lab result is outside its reference range. "
                "Worth discussing with your clinician at the next visit."
            ),
        )
    ]
