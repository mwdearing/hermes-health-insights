from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from health_insights.sqlite_ro import connect_readonly

TZ = ZoneInfo("America/Chicago")


def digest_line(db_path: str, date: str) -> str | None:
    """Return a one-line hydration summary for *date* (ISO 'YYYY-MM-DD').

    Chicago calendar date only.  Returns None when the DB or the samples
    table is missing so the caller can skip the line.
    """
    try:
        conn = connect_readonly(db_path)
    except Exception:
        return None

    try:
        cur = conn.execute(
            "SELECT start_time, value FROM samples WHERE type_code = 'hydration'"
        )
        rows = cur.fetchall()
    except Exception:
        # table missing or other SQL error
        return None
    finally:
        conn.close()

    # Sum mL for entries whose Chicago date matches *date*.
    target = datetime.fromisoformat(date).date()
    total_ml = 0.0
    for start_time_str, val in rows:
        if val is None:
            continue
        try:
            ts = datetime.fromisoformat(start_time_str)
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        chi_dt = ts.astimezone(TZ)
        if chi_dt.date() == target:
            total_ml += val

    if total_ml == 0:
        short = f"{target:%b} {int(target.day)}"
        return f"Water ({short}): none logged"

    litres = total_ml / 1000.0
    fl_oz = round(total_ml / 29.5735)
    short = f"{target:%b} {int(target.day)}"
    return f"Water ({short}): {litres:.1f} L ({fl_oz} fl oz)"
