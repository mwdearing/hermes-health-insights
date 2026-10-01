"""SpO2 and ECG concern rules.

Rules:
  spo2_critical   (level 3) — any SpO2 reading below 0.88 in the last
                      2 Chicago calendar days ending on ref_date.
  spo2_low_pair   (level 2) — two or more readings below 0.92 within
                      24 hours of each other (actual instants).
  spo2_low        (level 1) — any reading below 0.94 (but not critical
                      or pair).
  ecg_nonsinus    (level 2) — any ECG classification that is neither
                      exactly "Sinus Rhythm" nor starting with
                      "Inconclusive" in the last 30 Chicago days.
  ecg_inconclusive (level 1) — any ECG classification starting with
                      "Inconclusive" in the last 30 days.

Fewer than 3 readings, no readings, missing samples table, missing ECG
file, or missing ECG table → no findings, no crash.
"""
from __future__ import annotations

import sqlite3
from datetime import date as Date, datetime, timedelta
from typing import TYPE_CHECKING

from .. import ecg
from health_insights import settings
from health_insights.sqlite_ro import connect_readonly

if TYPE_CHECKING:
    from ..concerns import Finding


def spo2_ecg_findings(
    db_path: str,
    ref_date: Date | str,
    ecg_db: str = ecg.ECG_DB_PATH,
) -> list["Finding"]:
    """Return SpO2 and ECG concern findings for the Chicago day *ref_date*.

    Accepts a date object or a 'YYYY-MM-DD' string for *ref_date*.
    """
    from ..concerns import Finding  # late import to avoid circular import

    if isinstance(ref_date, str):
        ref_date = Date.fromisoformat(ref_date)

    from zoneinfo import ZoneInfo

    chi = settings.timezone()

    out: list[Finding] = []

    # --- SpO2 ---
    try:
        con = connect_readonly(db_path)
    except sqlite3.OperationalError:
        pass
    else:
        try:
            rows = con.execute(
                "SELECT start_time, value FROM samples WHERE type_code = 'oxygen_saturation'"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            con.close()

        if rows:
            # Convert start_time (ISO UTC) to Chicago datetime, filter to
            # the 2 Chicago calendar days ending on ref_date.
            ref_chicago = datetime(ref_date.year, ref_date.month, ref_date.day,
                                   0, 0, 0, tzinfo=chi)
            window_start = ref_chicago - timedelta(days=1)  # ref-1 00:00 Chicago
            window_end = ref_chicago + timedelta(days=1) - timedelta(seconds=1)  # ref 23:59:59 Chicago

            spo2_readings: list[tuple[datetime, float]] = []
            for ts_str, val in rows:
                # Parse ISO UTC timestamp
                try:
                    utc_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                chicago_dt = utc_dt.astimezone(chi)
                if window_start <= chicago_dt <= window_end:
                    spo2_readings.append((chicago_dt, val))

            if spo2_readings:
                # Check critical first (highest priority)
                critical = [(dt, v) for dt, v in spo2_readings if v < 0.88]
                if critical:
                    # Report the worst (lowest) reading
                    worst = min(critical, key=lambda x: x[1])
                    pct = int(round(worst[1] * 100))
                    day_str = worst[0].strftime("%Y-%m-%d")
                    out.append(
                        Finding(
                            id="spo2_critical",
                            level=3,
                            title="Critically low blood oxygen reading",
                            evidence=f"A reading of {pct}% was recorded on {day_str}",
                            source="common clinical threshold (spot readings are noisy)",
                            advice=(
                                "This is a critical low reading. Recheck your "
                                "device to confirm. If confirmed, or if you feel "
                                "unwell, seek care now."
                            ),
                        )
                    )
                else:
                    # Check for pair: two or more readings below 0.92 within 24h
                    low_readings = [(dt, v) for dt, v in spo2_readings if v < 0.92]
                    paired = False
                    if len(low_readings) >= 2:
                        for i in range(len(low_readings)):
                            for j in range(i + 1, len(low_readings)):
                                diff = abs((low_readings[i][0] - low_readings[j][0]).total_seconds())
                                if diff < 86400:  # strictly within 24 hours
                                    paired = True
                                    break
                            if paired:
                                break

                    if paired:
                        out.append(
                            Finding(
                                id="spo2_low_pair",
                                level=2,
                                title="Two low blood oxygen readings within 24 hours",
                                evidence="Two readings below 92% were recorded within 24 hours",
                                source="common clinical threshold (spot readings are noisy)",
                                advice=(
                                    "Multiple low readings in a short window "
                                    "may indicate a problem. Worth discussing "
                                    "with your clinician."
                                ),
                            )
                        )
                    else:
                        # Single low reading below 0.94
                        low94 = [(dt, v) for dt, v in spo2_readings if v < 0.94]
                        if low94:
                            worst = min(low94, key=lambda x: x[1])
                            pct = int(round(worst[1] * 100))
                            day_str = worst[0].strftime("%Y-%m-%d")
                            out.append(
                                Finding(
                                    id="spo2_low",
                                    level=1,
                                    title="Low blood oxygen reading",
                                    evidence=f"A reading of {pct}% was recorded on {day_str} (threshold: 94%)",
                                    source="common clinical threshold (spot readings are noisy)",
                                    advice=(
                                        "This reading is below the common "
                                        "clinical threshold. Spot readings "
                                        "are noisy; recheck if concerned."
                                    ),
                                )
                            )

    # --- ECG ---
    try:
        con = connect_readonly(ecg_db)
    except (sqlite3.OperationalError, OSError):
        pass
    else:
        try:
            rows = con.execute(
                "SELECT recorded_local_date, classification FROM ecg_recordings"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            con.close()

        if rows:
            # 30 Chicago calendar days ending on ref_date (ref-29 .. ref inclusive)
            ref_chicago = datetime(ref_date.year, ref_date.month, ref_date.day,
                                   0, 0, 0, tzinfo=chi)
            window_start = ref_chicago - timedelta(days=29)  # ref-29 00:00 Chicago
            window_end = ref_chicago + timedelta(days=1) - timedelta(seconds=1)  # ref 23:59:59 Chicago

            ecg_readings: list[tuple[str, str]] = []
            for local_date_str, classification in rows:
                try:
                    rec_date = Date.fromisoformat(local_date_str)
                except (ValueError, AttributeError):
                    continue
                # Compare as dates against the Chicago-day boundaries
                if window_start.date() <= rec_date <= window_end.date():
                    ecg_readings.append((local_date_str, classification))

            if ecg_readings:
                # Check for non-sinus first (highest priority)
                nonsinus = [
                    (d, c) for d, c in ecg_readings
                    if c != "Sinus Rhythm" and not c.startswith("Inconclusive")
                ]
                if nonsinus:
                    # Report the worst (most recent non-sinus)
                    worst = max(nonsinus, key=lambda x: x[0])
                    out.append(
                        Finding(
                            id="ecg_nonsinus",
                            level=2,
                            title="Non-sinus ECG classification detected",
                            evidence=f"An ECG recorded on {worst[0]} was classified as '{worst[1]}'",
                            source="Apple Watch ECG classifications",
                            advice=(
                                "This classification is not normal sinus rhythm. "
                                "Worth discussing with your clinician. If you have "
                                "chest pain, fainting, or breathlessness, seek care "
                                "now."
                            ),
                        )
                    )
                else:
                    # Check for inconclusive
                    inconclusive = [
                        (d, c) for d, c in ecg_readings
                        if c.startswith("Inconclusive")
                    ]
                    if inconclusive:
                        count = len(inconclusive)
                        out.append(
                            Finding(
                                id="ecg_inconclusive",
                                level=1,
                                title="Inconclusive ECG recordings detected",
                                evidence=f"{count} inconclusive ECG recording(s) in the last 30 days",
                                source="Apple Watch ECG classifications",
                                advice=(
                                    "Inconclusive results may be due to movement "
                                    "or poor contact. Retest if symptoms persist."
                                ),
                            )
                        )

    # Sort by level descending, then id ascending (same as bp_findings)
    out.sort(key=lambda f: (-f.level, f.id))
    return out
