"""
Read lab results from an Apple Health clinical-records export and persist them.

Data plumbing only. Not medical advice: a flag is only the value versus the
lab's own reference range (LOW / HIGH / NORMAL / UNKNOWN), never a diagnosis or
prediction.

Live DB (labs.sqlite) and its directory are created with owner-only modes so
the most sensitive data (lab results) never inherit group/other permissions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from health_insights import settings
from health_insights.sqlite_ro import connect_readonly

# ---- DB paths / modes -----------------------------------------------------

LABS_DB_DIR = str(settings.labs_dir())
LABS_DB_PATH = os.path.join(LABS_DB_DIR, "labs.sqlite")

# Reference range text patterns: a-b (both), <x (high only), >x (low only).
_RANGE_PAIR = re.compile(r"\s*([<>]?[+-]?\d+(?:\.\d+)?)\s*[-–—]\s*([<>]?[+-]?\d+(?:\.\d+)?)\s*")
_RANGE_LT = re.compile(r"\s*<([+-]?\d+(?:\.\d+)?)\s*")
_RANGE_GT = re.compile(r"\s*>([+-]?\d+(?:\.\d+)?)\s*")


@dataclass
class LabResult:
    obs_id: str
    loinc: Optional[str]
    name: str
    category: Optional[str]
    effective_date: str
    value_num: Optional[float]
    unit: Optional[str]
    value_text: Optional[str]
    ref_low: Optional[float]
    ref_high: Optional[float]
    ref_text: Optional[str]
    flag: str
    imported_at: str
    source_file: str


# ---- value / range parsing ------------------------------------------------

def _numeric(value) -> Optional[float]:
    """Best-effort float parse; None when not a plain number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _loinc(obs: dict) -> Optional[str]:
    """Return the first LOINC code from an observation or its components."""
    for source in (obs.get("code", {}), *_component_codes(obs)):
        code = source if isinstance(source, dict) else {}
        coding = code.get("coding") or []
        for c in coding:
            if isinstance(c, dict) and "loinc" in str(c.get("system", "")).lower():
                if c.get("code"):
                    return str(c["code"])
    return None


def _component_codes(obs: dict) -> list[dict]:
    """Return the `code` mapping from each component with a valueQuantity."""
    codes: list[dict] = []
    for comp in obs.get("component", []) or []:
        if isinstance(comp, dict) and comp.get("valueQuantity"):
            codes.append(comp.get("code", {}))
    return codes


def _name(obs: dict) -> str:
    code = obs.get("code", {})
    if isinstance(code, list):
        code = {}
    text = code.get("text")
    if text:
        return str(text)
    return ""


def _category(obs: dict) -> Optional[str]:
    cat = obs.get("category", {})
    if isinstance(cat, dict):
        coding = cat.get("coding") or []
    elif isinstance(cat, list):
        coding = []
        for c in cat:
            if isinstance(c, dict):
                coding.extend(c.get("coding") or [])
    else:
        coding = []
    for c in coding:
        if isinstance(c, dict) and c.get("code"):
            return str(c["code"])
    return None


def _effective_date(obs: dict) -> str:
    """Result date from effectiveDateTime (YYYY-MM-DD)."""
    for key in ("effectiveDateTime", "effectivePeriod"):
        if key not in obs:
            continue
        raw = obs[key]
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        if isinstance(raw, dict):  # effectivePeriod: {start, end}
            raw = raw.get("start", "")
        if isinstance(raw, str):
            return raw[:10]
    return ""


def _parse_ref_range(obs: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Return (low, high, text) from a single referenceRange entry."""
    low: Optional[float] = None
    high: Optional[float] = None
    text: Optional[str] = None
    for entry in obs.get("referenceRange") or []:
        low_q = _numeric(entry.get("low", {}).get("value"))
        high_q = _numeric(entry.get("high", {}).get("value"))
        if low_q is not None or high_q is not None:
            low = low_q
            high = high_q
            return low, high, None
        if entry.get("text"):
            text = str(entry["text"]).strip()
            m = _RANGE_PAIR.match(text)
            if m:
                low = _numeric(m.group(1))
                high = _numeric(m.group(2))
                if low is not None and high is not None:
                    return low, high, None
            m = _RANGE_LT.match(text)
            if m:
                high = _numeric(m.group(1))
                return None, high, None
            m = _RANGE_GT.match(text)
            if m:
                low = _numeric(m.group(1))
                return low, None, None
    return low, high, text


def _parse_value(obs: dict) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Return (value_num, unit, value_text)."""
    if "valueQuantity" in obs:
        vq = obs["valueQuantity"] or {}
        return _numeric(vq.get("value")), vq.get("unit"), None
    if "valueString" in obs:
        raw = obs["valueString"]
        if raw is None:
            return None, None, None
        s = str(raw).strip()
        if not s:
            return None, None, None
        num = _numeric(s)
        if num is not None:
            return num, None, None
        return None, None, s
    return None, None, None


def _flag(value_num, low, high) -> str:
    """Flag a numeric value against a (possibly one-sided) reference range."""
    if value_num is None:
        return "UNKNOWN"
    if low is not None and value_num < low:
        return "LOW"
    if high is not None and value_num > high:
        return "HIGH"
    if low is not None or high is not None:
        return "NORMAL"
    return "UNKNOWN"


# ---- shared per-observation logic -----------------------------------------

def results_from_observations(obs_iter) -> list[LabResult]:
    """Build LabResult rows from an iterable of (source_name, observation_dict).

    This is the shared core used by both the zip-based reader and the JSON
    importer so the two paths cannot drift.
    """
    results: list[LabResult] = []
    for source_file, obs in obs_iter:
        category = _category(obs)
        obs_id = obs.get("id")
        if not obs_id:
            continue
        loinc = _loinc(obs)
        name = _name(obs)
        if not name and loinc:
            name = f"LOINC {loinc}"
        effective_date = _effective_date(obs)
        value_num, unit, value_text = _parse_value(obs)
        ref_low, ref_high, ref_text = _parse_ref_range(obs)
        # BP (and similar) have no main value: borrow the first numeric
        # LOINC component (systolic) so the row counts as numeric.
        if value_num is None:
            for comp in obs.get("component", []) or []:
                if not isinstance(comp, dict):
                    continue
                comp_code = comp.get("code", {}) if isinstance(comp.get("code"), dict) else {}
                comp_loinc = _loinc_from_code(comp_code)
                if not comp_loinc:
                    continue
                vq = comp.get("valueQuantity") or {}
                comp_num = _numeric(vq.get("value"))
                if comp_num is not None:
                    value_num = comp_num
                    unit = vq.get("unit")
                    loinc = comp_loinc
                    name = f"{name} ({comp_loinc})" if name else f"LOINC {comp_loinc}"
                    break
        flag = _flag(value_num, ref_low, ref_high)
        results.append(
            LabResult(
                obs_id=obs_id,
                loinc=loinc,
                name=name,
                category=category,
                effective_date=effective_date,
                value_num=value_num,
                unit=unit,
                value_text=value_text,
                ref_low=ref_low,
                ref_high=ref_high,
                ref_text=ref_text,
                flag=flag,
                imported_at=datetime.now(timezone.utc).isoformat(),
                source_file=source_file,
            )
        )
    return results


# ---- streaming reader (zip) ------------------------------------------------

def _iter_observations(zip_path: str):
    """Stream Observation members from a zip (never unpacked to disk)."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            with zf.open(name) as fp:
                doc = json.load(fp)
            if isinstance(doc, dict) and doc.get("resourceType") == "Observation":
                yield name, doc


def read_labs(zip_path: str) -> list[LabResult]:
    """Read all lab observations from a clinical-records export.

    One row per Observation record (category-agnostic). Blood Pressure, which
    carries no main value, is populated from its systolic component so it counts
    as numeric; components without a LOINC code are ignored, which keeps
    synthetic FAKE fixtures from inflating counts.
    """
    return results_from_observations(_iter_observations(zip_path))


# ---- JSON reader (FHIR) ----------------------------------------------------

def read_labs_json(path: str) -> list[LabResult]:
    """Read lab observations from a JSON file.

    Accepts a JSON list of FHIR Observation dicts or a FHIR Bundle with
    entry[].resource.  Raises ``ValueError`` for anything else (a single
    object, not-JSON, etc.).  An empty list returns ``[]``.

    Uses ``source_file`` = ``<file name>#<index>``.
    """
    with open(path, "r", encoding="utf-8") as fp:
        try:
            data = json.load(fp)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Not valid JSON: {exc}") from exc

    # Empty list -> nothing to do
    if isinstance(data, list):
        if len(data) == 0:
            return []
        # Filter to Observations only
        obs_iter = (
            (f"{os.path.basename(path)}#{idx}", doc)
            for idx, doc in enumerate(data)
            if isinstance(doc, dict) and doc.get("resourceType") == "Observation"
        )
        return results_from_observations(obs_iter)

    # Bundle?
    if isinstance(data, dict) and data.get("resourceType") == "Bundle":
        entries = data.get("entry") or []
        obs_iter = (
            (f"{os.path.basename(path)}#{idx}", entry.get("resource", {}))
            for idx, entry in enumerate(entries)
            if isinstance(entry, dict)
            and isinstance(entry.get("resource"), dict)
            and entry["resource"].get("resourceType") == "Observation"
        )
        return results_from_observations(obs_iter)

    # Single object (not a list, not a Bundle) -> error
    raise ValueError(
        f"Expected a JSON list or FHIR Bundle, got {data.get('resourceType', type(data).__name__)}"
    )


def _loinc_from_code(code: dict) -> Optional[str]:
    """Return the first LOINC code from a raw `code` mapping."""
    if not isinstance(code, dict):
        return None
    for c in code.get("coding") or []:
        if isinstance(c, dict) and "loinc" in str(c.get("system", "")).lower():
            if c.get("code"):
                return str(c["code"])
    return None


# ---- database -------------------------------------------------------------

def ensure_db_dir(db_dir: str = LABS_DB_DIR) -> None:
    """Create the DB directory owner-only (0700).

    A directory this call creates, and the live labs directory, are made 0700. A pre-existing directory the
    caller merely named (a shared scratch dir, a test tmp dir) keeps its mode: chmod'ing arbitrary parents
    was a review finding (2026-09-22).
    """
    created = not os.path.isdir(db_dir)
    os.makedirs(db_dir, mode=0o700, exist_ok=True)
    if created or os.path.abspath(db_dir) == os.path.abspath(LABS_DB_DIR):
        os.chmod(db_dir, 0o700)


def open_db(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the live DB with owner-only file modes.

    The umask below only governs a file sqlite creates fresh; it does nothing for a file that already
    exists (copied in, or created before this code existed). Explicitly chmod any existing file too
    (2026-09-22 review) so it is owner-only regardless of how it got there.
    """
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
        CREATE TABLE IF NOT EXISTS lab_results (
            obs_id TEXT PRIMARY KEY,
            loinc TEXT,
            name TEXT,
            category TEXT,
            effective_date TEXT,
            value_num REAL,
            unit TEXT,
            value_text TEXT,
            ref_low REAL,
            ref_high REAL,
            ref_text TEXT,
            flag TEXT,
            imported_at TEXT,
            source_file TEXT
        )
        """
    )
    conn.commit()
    return conn


def insert_results(conn: sqlite3.Connection, results: list[LabResult]) -> dict[str, int]:
    """Insert with INSERT OR IGNORE keyed by obs_id. Returns counts."""
    cur = conn.cursor()
    new = 0
    already = 0
    for r in results:
        cur.execute(
            """
            INSERT OR IGNORE INTO lab_results (
                obs_id, loinc, name, category, effective_date,
                value_num, unit, value_text, ref_low, ref_high, ref_text,
                flag, imported_at, source_file
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?, ?, ?, ?)
            """,
            (
                r.obs_id, r.loinc, r.name, r.category, r.effective_date,
                r.value_num, r.unit, r.value_text, r.ref_low, r.ref_high, r.ref_text,
                r.flag, r.imported_at, r.source_file,
            ),
        )
        if cur.rowcount:
            new += cur.rowcount
        else:
            already += 1
    conn.commit()
    return {"new": new, "already_present": already}


def count_observation_files(zip_path: str) -> int:
    """Count Observation members in a zip (records read)."""
    n = 0
    for name, _doc in _iter_observations(zip_path):
        n += 1
    return n


def cmd_import_json(json_path: str, db_path: str) -> int:
    """Import lab results from a FHIR JSON file.

    Prints the same summary line as cmd_import:
    ``Labs import: N records read, X new, Y already present``.
    Idempotent via INSERT OR IGNORE keyed by obs_id.
    """
    results = read_labs_json(json_path)
    obs_count = len(results)
    ensure_db_dir(os.path.dirname(db_path))
    conn = open_db(db_path)
    counts = insert_results(conn, results)
    conn.close()
    print(
        f"Labs import: {obs_count} records read, "
        f"{counts['new']} new, {counts['already_present']} already present"
    )
    return 0


def _content_key(r: LabResult) -> tuple:
    """A same-observation key that holds across import paths.

    The bridge's client_record_id is a one-way hash of the FHIR observation id
    (it must match the ``hk-...``/``synthetic-...`` receiver pattern), so it can
    never match the raw FHIR id an export.zip JSON import used as obs_id for the
    SAME physical result. Compare by content instead: date + code + value, the
    same "same real-world record, different id" problem H-3 solved for ECG by
    comparing instants instead of member ids.
    """
    value = round(r.value_num, 6) if r.value_num is not None else r.value_text
    return (r.effective_date, r.loinc or r.name, value)


def read_labs_bridge(bridge_db: str) -> tuple[list[LabResult], list[str]]:
    """Read lab results from the HealthRelay bridge database.

    Opens the bridge READ-ONLY via a URI with the path percent-encoded.
    If the table is missing returns ([], []).
    """
    conn = connect_readonly(bridge_db)
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lab_results'"
        ).fetchone()
        if tbl is None:
            return ([], [])

        rows = conn.execute(
            """
            SELECT client_record_id, loinc, name, category, effective_date,
                   value_num, unit, value_text, ref_low, ref_high, ref_text
            FROM lab_results
            """
        ).fetchall()
    finally:
        conn.close()

    results: list[LabResult] = []
    rejected: list[str] = []
    imported_at = datetime.now(timezone.utc).isoformat()

    for row in rows:
        (client_record_id, loinc, name, category, effective_date,
         value_num, unit, value_text, ref_low, ref_high, ref_text) = row

        if not effective_date or not name:
            rejected.append(str(client_record_id))
            continue

        results.append(
            LabResult(
                obs_id=f"bridge:{client_record_id}",
                loinc=loinc,
                name=name,
                category=category,
                effective_date=effective_date,
                value_num=value_num,
                unit=unit,
                value_text=value_text,
                ref_low=ref_low,
                ref_high=ref_high,
                ref_text=ref_text,
                flag=_flag(value_num, ref_low, ref_high),
                imported_at=imported_at,
                source_file=f"bridge:{client_record_id}",
            )
        )

    return results, rejected


def cmd_import_bridge(bridge_db: str, labs_db: str) -> int:
    """Import lab results from the bridge DB into labs.sqlite with dedupe.

    Before inserting, skip any result whose content key (effective_date, LOINC
    or name, value) already exists in lab_results -- the same result may
    already be present from an export.zip JSON import under a different
    obs_id (see ``_content_key``).
    """
    results, rejected = read_labs_bridge(bridge_db)

    new_results = []
    already_present = 0

    if results:
        ensure_db_dir(os.path.dirname(labs_db))
        conn = open_db(labs_db)
        try:
            existing_rows = conn.execute(
                "SELECT effective_date, loinc, name, value_num, value_text FROM lab_results"
            ).fetchall()
            existing_keys = {
                (
                    eff_date,
                    loinc or name,
                    round(value_num, 6) if value_num is not None else value_text,
                )
                for eff_date, loinc, name, value_num, value_text in existing_rows
            }
        finally:
            conn.close()

        for r in results:
            if _content_key(r) in existing_keys:
                already_present += 1
            else:
                new_results.append(r)
                existing_keys.add(_content_key(r))

    if new_results:
        ensure_db_dir(os.path.dirname(labs_db))
        conn = open_db(labs_db)
        counts = insert_results(conn, new_results)
        conn.close()
        new_count = counts["new"]
        already_present += counts["already_present"]
    else:
        new_count = 0

    print(f"Labs import: {new_count} new, {already_present} already present, {len(rejected)} rejected")
    return 0


# ---- CLI helpers ----------------------------------------------------------

def _open_ro(db_path: str) -> sqlite3.Connection:
    return connect_readonly(db_path)


def _query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _latest_date(conn: sqlite3.Connection) -> Optional[str]:
    rows = _query(conn, "SELECT DISTINCT effective_date FROM lab_results WHERE effective_date IS NOT NULL AND effective_date != '' ORDER BY effective_date DESC")
    return rows[0][0] if rows else None


def _distinct_dates(conn: sqlite3.Connection) -> int:
    rows = _query(conn, "SELECT COUNT(DISTINCT effective_date) FROM lab_results WHERE effective_date IS NOT NULL AND effective_date != ''")
    return rows[0][0] if rows else 0


def _count_numeric(conn: sqlite3.Connection) -> int:
    rows = _query(conn, "SELECT COUNT(*) FROM lab_results WHERE value_num IS NOT NULL")
    return rows[0][0] if rows else 0


def _flag_counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    rows = _query(conn, "SELECT COUNT(*) FROM lab_results WHERE flag='HIGH'")
    high = rows[0][0]
    rows = _query(conn, "SELECT COUNT(*) FROM lab_results WHERE flag='LOW'")
    low = rows[0][0]
    rows = _query(conn, "SELECT COUNT(*) FROM lab_results WHERE flag NOT IN ('LOW','HIGH')")
    other = rows[0][0]
    return high, low, other


def cmd_import(zip_path: str, db_path: str) -> int:
    obs_count = count_observation_files(zip_path)
    results = read_labs(zip_path)
    ensure_db_dir(os.path.dirname(db_path))
    conn = open_db(db_path)
    counts = insert_results(conn, results)
    conn.close()
    print(
        f"Labs import: {obs_count} records read, "
        f"{counts['new']} new, {counts['already_present']} already present"
    )
    return 0


def cmd_summary(db_path: str) -> int:
    if not os.path.exists(db_path):
        print(f"No labs data at {db_path}")
        return 0
    conn = _open_ro(db_path)
    total = _query(conn, "SELECT COUNT(*) FROM lab_results")[0][0]
    dates = _distinct_dates(conn)
    latest = _latest_date(conn)
    numeric = _count_numeric(conn)
    high, low, other = _flag_counts(conn)
    outside = high + low
    latest_str = latest if latest else "none"
    print(
        f"Labs: {total} results on {dates} dates, "
        f"latest {latest_str}; {numeric} numeric; "
        f"{outside} outside range (HIGH {high}, LOW {low})"
    )
    conn.close()
    return 0


def cmd_trend(db_path: str, loinc: str) -> int:
    if not os.path.exists(db_path):
        print(f"No labs data at {db_path}")
        return 0
    conn = _open_ro(db_path)
    rows = _query(
        conn,
        "SELECT value_num, effective_date FROM lab_results "
        "WHERE loinc=? AND value_num IS NOT NULL "
        "ORDER BY effective_date ASC",
        (loinc,),
    )
    conn.close()
    numeric = [v for v, _ in rows]
    if len(numeric) < 2:
        print(f"{loinc}: {len(numeric)} values, latest vs previous: n/a")
        return 0
    latest = numeric[-1]
    previous = numeric[-2]
    if latest > previous:
        direction = "up"
    elif latest < previous:
        direction = "down"
    else:
        direction = "flat"
    print(f"{loinc}: {len(numeric)} values, latest vs previous: {direction}")
    return 0


def cmd_digest_line(db_path: str, date: str) -> int:
    """Print a digest line for results imported in the 7 days up to --date."""
    if not os.path.exists(db_path):
        return 0
    if not date:
        return 0
    conn = _open_ro(db_path)
    # imported_at is UTC ISO while --date is a Chicago day: convert each timestamp to its Chicago date before
    # comparing (a late-evening import is already the next UTC day; review 2026-09-22).
    rows = _query(conn, "SELECT imported_at, flag FROM lab_results WHERE imported_at IS NOT NULL")
    conn.close()
    from datetime import date as _date, timedelta
    from zoneinfo import ZoneInfo
    chi = settings.timezone()
    end = _date.fromisoformat(date)
    start = end - timedelta(days=7)
    new_results = outside = 0
    for imported_at, flag in rows:
        try:
            ts = datetime.fromisoformat(imported_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        day = ts.astimezone(chi).date()
        if start <= day <= end:
            new_results += 1
            if flag in ("LOW", "HIGH"):
                outside += 1
    if new_results:
        # "imported" not "new": these are results that entered the labs db in the last 7 days (an initial import
        # of years of history reads as "33 new results" otherwise)
        print(f"Labs: {new_results} results imported in the last 7 days, {outside} outside range")
    return 0
