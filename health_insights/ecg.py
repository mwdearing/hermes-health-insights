"""
Read Apple Watch ECG records from an Apple Health export zip and persist them.

Data plumbing only. Each ECG is stored as metadata only (recorded time, Chicago local date,
classification, sample rate, sample count, duration, device, symptoms) — the ~30 s of voltage
samples are COUNTED, never stored, so the DB never holds raw voltages (never sent anywhere).

Live DB (ecg.sqlite) and its directory are created with owner-only modes so the most sensitive
data (a heart-rhythm recording's metadata) never inherit group/other permissions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from health_insights import settings
from health_insights.sqlite_ro import connect_readonly

CHI = settings.timezone()

# ---- DB paths / modes -----------------------------------------------------

ECG_DB_DIR = str(settings.ecg_dir())
ECG_DB_PATH = os.path.join(ECG_DB_DIR, "ecg.sqlite")


@dataclass
class EcgRecording:
    member: str
    recorded_at: str          # UTC ISO with offset
    recorded_local_date: str  # America/Chicago calendar date
    classification: str
    symptoms: str
    device: str
    software_version: str
    sample_rate: Optional[int]
    sample_count: int
    duration_s: float
    lead: str
    unit: str
    imported_at: str
    source_file: str


# ---- ECG CSV parsing ------------------------------------------------------

# Recorded Date / Sample Rate / Classification / Symptoms / Device / Software Version / Lead / Unit
_REC_RE = re.compile(r"^Recorded Date\s*,\s*(.+)$")
_REC_FORMATS = ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z")
# A sample row is an all-numeric line: Apple writes decimal microvolts (e.g. -12.345), so integers alone
# would find the first sample hundreds of lines late (real export 2026-09-22: 14403 of 15360 counted).
# This is the boundary we stop the header search at.
_SAMPLE_ROW_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _parse_recorded_date(raw: str) -> Optional[str]:
    """Return the UTC ISO instant for an Apple 'Recorded Date' line, or None if unparseable."""
    raw = raw.strip()
    if not raw:
        return None
    for fmt in _REC_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _parse_header(lines) -> dict:
    """Parse the key/value header lines of an ECG CSV.

    Real Apple ECG CSVs have two blank lines *before* the Lead/Unit lines (which
    come after the first blank line), so we cannot stop at the first blank line.
    We parse every key/value line up to the first sample row (an all-numeric
    line of integer microvolts); that is the definitive end of the header.
    """
    fields: dict[str, str] = {}
    for line in lines:
        if _SAMPLE_ROW_RE.match(line.strip()):
            break  # first sample row ends the header
        if "," in line:
            k, _, v = line.partition(",")
            fields[k.strip()] = _unquote(v.strip())
    return fields


def _unquote(v: str) -> str:
    """Strip a single pair of surrounding double quotes (CSV fields containing commas)."""
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    return v


def _read_ecg_members(zip_path: str):
    """Stream electrocardiograms members from a zip (never unpacked to disk)."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.startswith("apple_health_export/electrocardiograms/") and name.endswith(".csv"):
                yield name, zf.open(name)


def read_ecgs(zip_path: str) -> tuple[list[EcgRecording], list[str]]:
    """Read ECG metadata from an export zip.

    Returns (recordings, rejected_members). Each CSV is parsed by streaming its header until the blank
    line and then COUNTING the sample lines (never storing the voltages). A CSV with no usable header is
    counted as rejected, never raised.
    """
    recordings: list[EcgRecording] = []
    rejected: list[str] = []
    for member, handle in _read_ecg_members(zip_path):
        try:
            text = handle.read().decode("utf-8", errors="replace")
        except Exception:
            rejected.append(member)
            continue
        lines = text.splitlines()
        if not lines:
            rejected.append(member)
            continue
        fields = _parse_header(lines)
        recorded = _parse_recorded_date(fields.get("Recorded Date", ""))
        if recorded is None:
            rejected.append(member)
            continue
        # Count the sample lines: everything from the first all-numeric row onward.
        # That row is itself a sample, so it is included in the count.
        idx = len(lines)
        for i, ln in enumerate(lines):
            if _SAMPLE_ROW_RE.match(ln.strip()):
                idx = i
                break
        sample_count = sum(1 for ln in lines[idx:] if ln.strip())
        rate_raw = fields.get("Sample Rate", "")
        sample_rate = None
        m = re.search(r"(\d+)", rate_raw)
        if m:
            sample_rate = int(m.group(1))
        duration_s = round(sample_count / sample_rate, 3) if sample_rate else 0.0
        dt = datetime.fromisoformat(recorded)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        recordings.append(
            EcgRecording(
                member=os.path.basename(member),
                recorded_at=recorded,
                recorded_local_date=dt.astimezone(CHI).strftime("%Y-%m-%d"),
                classification=fields.get("Classification", ""),
                symptoms=fields.get("Symptoms", ""),
                device=fields.get("Device", ""),
                software_version=fields.get("Software Version", ""),
                sample_rate=sample_rate,
                sample_count=sample_count,
                duration_s=duration_s,
                lead=fields.get("Lead", ""),
                unit=fields.get("Unit", ""),
                imported_at=datetime.now(timezone.utc).isoformat(),
                source_file=member,
            )
        )
    return recordings, rejected


# ---- ECG JSON parsing (phone app payload) ---------------------------------

def _parse_json_date(raw: str) -> Optional[str]:
    """Return a UTC ISO instant for an ISO-8601 *raw* with offset, or None."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def read_ecgs_json(path) -> tuple[list[EcgRecording], list[str]]:
    """Import ECG recordings from a JSON file (phone app payload).

    *path* may be a file path (str/Path) or an already-loaded list/obj.
    Returns (recordings, rejected).  The voltage samples are COUNTED, never
    stored (privacy rule).
    """
    # ---- load JSON --------------------------------------------------------
    if isinstance(path, (str, os.PathLike)):
        p = Path(path)
        if not p.is_file():
            raise ValueError(
                "Top-level JSON must be a list of recordings or an object with a 'recordings' key"
            )
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"Cannot read JSON file: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
    else:
        data = path  # already a list or dict

    # ---- normalise to list of recording dicts -----------------------------
    if isinstance(data, list):
        raw_recordings: list[dict] = data
    elif isinstance(data, dict):
        recs = data.get("recordings")
        if not isinstance(recs, list):
            raise ValueError(
                "JSON object must contain a 'recordings' list key"
            )
        raw_recordings = recs
    else:
        raise ValueError(
            "Top-level JSON must be a list of recordings or an object with a 'recordings' key"
        )

    # ---- build EcgRecording objects ---------------------------------------
    recordings: list[EcgRecording] = []
    rejected: list[str] = []

    for rec in raw_recordings:
        rid = rec.get("id")
        recorded_at_raw = rec.get("recorded_at")

        # Reject: missing id or recorded_at
        if not rid or not recorded_at_raw:
            rejected.append(str(rid or "<missing id>"))
            continue

        # Reject: unparsable date
        recorded_at = _parse_json_date(recorded_at_raw)
        if recorded_at is None:
            rejected.append(str(rid))
            continue

        # Parse sampling_frequency_hz → int
        sf = rec.get("sampling_frequency_hz")
        try:
            sample_rate = int(sf) if sf is not None else None
        except (TypeError, ValueError):
            sample_rate = None

        voltages = rec.get("voltages", [])
        sample_count = len(voltages)  # counted, never stored
        duration_s = round(sample_count / sample_rate, 3) if sample_rate else 0.0

        dt = datetime.fromisoformat(recorded_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        recordings.append(
            EcgRecording(
                member=str(rid),
                recorded_at=recorded_at,
                recorded_local_date=dt.astimezone(CHI).strftime("%Y-%m-%d"),
                classification=rec.get("classification", ""),
                symptoms=rec.get("symptoms", ""),
                device=rec.get("device", ""),
                software_version=rec.get("software_version", ""),
                sample_rate=sample_rate,
                sample_count=sample_count,
                duration_s=duration_s,
                lead=rec.get("lead", ""),
                unit=rec.get("unit", ""),
                imported_at=datetime.now(timezone.utc).isoformat(),
                source_file=str(path) if isinstance(path, (str, os.PathLike)) else "json",
            )
        )

    return recordings, rejected


# ---- database -------------------------------------------------------------

def ensure_db_dir(db_dir: str = ECG_DB_DIR) -> None:
    """Create the DB directory owner-only (0700)."""
    created = not os.path.isdir(db_dir)
    os.makedirs(db_dir, mode=0o700, exist_ok=True)
    if created or os.path.abspath(db_dir) == os.path.abspath(ECG_DB_DIR):
        os.chmod(db_dir, 0o700)


def open_db(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the live DB with owner-only file modes."""
    db_dir = os.path.dirname(db_path)
    if db_dir:
        ensure_db_dir(db_dir)
    old_umask = os.umask(0o077)
    try:
        conn = sqlite3.connect(db_path)
    finally:
        os.umask(old_umask)
    if os.path.exists(db_path):
        os.chmod(db_path, 0o600)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ecg_recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member TEXT,
            recorded_at TEXT,
            recorded_local_date TEXT,
            classification TEXT,
            symptoms TEXT,
            device TEXT,
            software_version TEXT,
            sample_rate INTEGER,
            sample_count INTEGER,
            duration_s REAL,
            lead TEXT,
            unit TEXT,
            imported_at TEXT,
            source_file TEXT,
            UNIQUE(member, recorded_at)
        )
        """
    )
    conn.commit()
    return conn


def insert_recordings(conn: sqlite3.Connection, recordings: list[EcgRecording]) -> dict[str, int]:
    """Insert with INSERT OR IGNORE keyed on (member, recorded_at). Returns counts."""
    cur = conn.cursor()
    new = 0
    already = 0
    for r in recordings:
        cur.execute(
            """
            INSERT OR IGNORE INTO ecg_recordings (
                member, recorded_at, recorded_local_date, classification, symptoms, device,
                software_version, sample_rate, sample_count, duration_s, lead, unit, imported_at, source_file
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?, ?, ?, ?)
            """,
            (
                r.member, r.recorded_at, r.recorded_local_date, r.classification, r.symptoms, r.device,
                r.software_version, r.sample_rate, r.sample_count, r.duration_s, r.lead, r.unit,
                r.imported_at, r.source_file,
            ),
        )
        if cur.rowcount:
            new += cur.rowcount
        else:
            already += 1
    conn.commit()
    return {"new": new, "already_present": already}


# ---- bridge import --------------------------------------------------------

_CLASS_MAP = {
    "sinus_rhythm": "Sinus Rhythm",
    "atrial_fibrillation": "Atrial Fibrillation",
    "inconclusive_low_heart_rate": "Inconclusive (Low Heart Rate)",
    "inconclusive_high_heart_rate": "Inconclusive (High Heart Rate)",
    "inconclusive_poor_reading": "Inconclusive (Poor Reading)",
    "inconclusive_other": "Inconclusive",
    "unrecognized": "Unrecognized",
    "not_set": "Not Set",
}

_SYMPTOMS_MAP = {
    "none": "None",
    "present": "Present",
    "not_set": "Not Set",
}


def read_ecgs_bridge(bridge_db: str) -> tuple[list[EcgRecording], list[str]]:
    """Read ECG recordings from the HealthRelay bridge database.

    Opens the bridge READ-ONLY via a URI with the path percent-encoded.
    If the table is missing returns ([], []).
    """
    conn = connect_readonly(bridge_db)
    try:
        # Check if table exists
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='electrocardiograms'"
        ).fetchone()
        if tbl is None:
            return ([], [])

        rows = conn.execute(
            """
            SELECT client_record_id, start_time, end_time, classification,
                   symptoms_status, average_heart_rate_bpm, sampling_frequency_hz,
                   voltage_count, voltages_json
            FROM electrocardiograms
            """
        ).fetchall()
    finally:
        conn.close()

    recordings: list[EcgRecording] = []
    rejected: list[str] = []

    for row in rows:
        (client_record_id, start_time, end_time, classification,
         symptoms_status, avg_hr, sampling_freq, voltage_count, voltages_json) = row

        # Parse start_time to UTC datetime
        try:
            if start_time.endswith("Z"):
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            else:
                start_dt = datetime.fromisoformat(start_time)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            recorded_at_local = start_dt.astimezone(CHI).isoformat()
        except (ValueError, TypeError):
            rejected.append(str(client_record_id))
            continue

        # Convert to Chicago local
        chicago_dt = start_dt.astimezone(CHI)
        recorded_local_date = chicago_dt.strftime("%Y-%m-%d")

        # Classification mapping
        bridge_class = classification or ""
        classification_mapped = _CLASS_MAP.get(bridge_class, bridge_class)

        # Symptoms mapping
        bridge_sym = symptoms_status or ""
        symptoms_mapped = _SYMPTOMS_MAP.get(bridge_sym, bridge_sym)

        # Parse sample rate
        sample_rate = None
        if sampling_freq:
            try:
                sample_rate = int(sampling_freq)
            except (TypeError, ValueError):
                pass

        # Duration
        duration_s = 30.0
        if end_time and sampling_freq:
            try:
                if end_time.endswith("Z"):
                    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                else:
                    end_dt = datetime.fromisoformat(end_time)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                duration_s = (end_dt - start_dt).total_seconds()
            except (ValueError, TypeError):
                pass

        # Voltage count
        sample_count = int(voltage_count) if voltage_count is not None else 0

        recordings.append(
            EcgRecording(
                member=client_record_id,
                recorded_at=recorded_at_local,
                recorded_local_date=recorded_local_date,
                classification=classification_mapped,
                symptoms=symptoms_mapped,
                device="HealthRelay bridge",
                software_version="",
                sample_rate=sample_rate,
                sample_count=sample_count,
                duration_s=duration_s,
                lead="Lead I",
                unit="µV",
                imported_at=datetime.now(timezone.utc).isoformat(),
                source_file=f"bridge:{client_record_id}",
            )
        )

    return recordings, rejected


def cmd_import_bridge(bridge_db: str, ecg_db: str) -> int:
    """Import ECG recordings from the bridge DB into ecg.sqlite with dedupe.

    Before inserting, skip any recording whose recorded_at instant (as UTC
    datetime) already exists in ecg_recordings.
    """
    recordings, rejected = read_ecgs_bridge(bridge_db)

    # Dedupe: find which recordings already exist by comparing recorded_at as UTC datetime
    new_recordings = []
    already_present = 0

    if recordings:
        ensure_db_dir(os.path.dirname(ecg_db))
        conn = open_db(ecg_db)
        try:
            # Get all existing recorded_at values
            existing = conn.execute(
                "SELECT recorded_at FROM ecg_recordings"
            ).fetchall()

            # Parse existing recorded_at into UTC datetimes for comparison
            existing_times: set[datetime] = set()
            for (ra,) in existing:
                try:
                    dt = datetime.fromisoformat(ra)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    existing_times.add(dt.astimezone(timezone.utc))
                except (ValueError, TypeError):
                    pass

            for rec in recordings:
                rec_dt = datetime.fromisoformat(rec.recorded_at)
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                rec_utc = rec_dt.astimezone(timezone.utc)

                if rec_utc in existing_times:
                    already_present += 1
                else:
                    new_recordings.append(rec)
        finally:
            conn.close()

    # Insert new recordings
    if new_recordings:
        ensure_db_dir(os.path.dirname(ecg_db))
        conn = open_db(ecg_db)
        counts = insert_recordings(conn, new_recordings)
        conn.close()
        new_count = counts["new"]
        already_present += counts["already_present"]
    else:
        new_count = 0

    print(f"ECG import: {new_count} new, {already_present} already present, {len(rejected)} rejected")
    return 0


# ---- CLI helpers ----------------------------------------------------------

def _open_ro(db_path: str) -> sqlite3.Connection:
    return connect_readonly(db_path)


def cmd_import(zip_path: str, db_path: str) -> int:
    recordings, rejected = read_ecgs(zip_path)
    ensure_db_dir(os.path.dirname(db_path))
    conn = open_db(db_path)
    counts = insert_recordings(conn, recordings)
    conn.close()
    print(f"ECG import: {counts['new']} new, {counts['already_present']} already present, {len(rejected)} rejected")
    return 0


def cmd_import_json(json_path: str, db_path: str) -> int:
    recordings, rejected = read_ecgs_json(json_path)
    ensure_db_dir(os.path.dirname(db_path))
    conn = open_db(db_path)
    counts = insert_recordings(conn, recordings)
    conn.close()
    print(f"ECG import: {counts['new']} new, {counts['already_present']} already present, {len(rejected)} rejected")
    return 0


def cmd_summary(db_path: str) -> int:
    if not os.path.exists(db_path):
        print(f"No ECG data at {db_path}")
        return 0
    conn = _open_ro(db_path)
    total = conn.execute("SELECT COUNT(*) FROM ecg_recordings").fetchone()[0]
    latest = conn.execute("SELECT MAX(recorded_at) FROM ecg_recordings").fetchone()[0]
    latest_str = latest if latest else "none"
    print(f"ECG: {total} recordings, latest {latest_str}")
    conn.close()
    return 0


def cmd_digest_line(db_path: str, date: str) -> int:
    """Print a digest line for ECG recordings in the 7 days up to --date (counts + classifications only)."""
    if not os.path.exists(db_path):
        return 0
    if not date:
        return 0
    conn = _open_ro(db_path)
    rows = conn.execute(
        "SELECT recorded_at, recorded_local_date, classification FROM ecg_recordings"
    ).fetchall()
    conn.close()
    end = datetime.fromisoformat(date).date()
    start = end
    from datetime import timedelta
    start = end - timedelta(days=7)
    counts: dict[str, int] = {}
    for recorded_at, recorded_local_date, classification in rows:
        try:
            d = datetime.fromisoformat(recorded_local_date).date()
        except (ValueError, TypeError):
            continue
        if start <= d <= end:
            key = classification or "Unknown"
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return 0
    parts = ", ".join(f"{counts[k]} {k}" for k in sorted(counts))
    total = sum(counts.values())
    plural = "" if total == 1 else "s"
    print(f"ECG: {total} recording{plural} in the last 7 days: {parts}")
    return 0
