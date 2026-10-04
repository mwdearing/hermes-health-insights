"""Compound and upper-limit (UL) sections of the nutrition report, from the intake context.

Facts and statuses only. All amounts are Decimal. Every amount has exactly one owner:
an Apple dietary sample that an effective intake component actively links to is
attributed to that component and counted once; an unlinked sample has source
"unknown"; an unlinked intake nutrient component contributes its own amount.
Missing intake tables give empty results, never an error.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

from health_insights.evidence import compound_exposure_summary
from health_insights.intake_reader import IntakeComponent, read_effective_components
from health_insights.ul_attribution import Contribution, ULFinding, compare_to_ul

_LINKS_SQL = """
SELECT r.owner_id, r.producer_id, r.intake_id, l.component_id, l.sample_uuid
FROM intake_sample_links l
JOIN intake_revisions r ON r.intake_revision_row_id = l.intake_revision_row_id
WHERE l.disposition = 'active'
  AND r.revision = (SELECT MAX(r2.revision) FROM intake_revisions r2
                    WHERE r2.owner_id = r.owner_id AND r2.producer_id = r.producer_id
                      AND r2.intake_id = r.intake_id)
  AND l.projection_sequence = (SELECT MAX(s.projection_sequence) FROM intake_projection_snapshots s
                               WHERE s.intake_revision_row_id = l.intake_revision_row_id)
"""


def _dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def fmt_decimal(d: Optional[Decimal]) -> str:
    """Plain decimal text, at most two decimals, no exponent."""
    if d is None:
        return "unknown"
    return format(d.quantize(Decimal("0.01")).normalize(), "f")


def _local_day(occurred_at: str, time_zone: str) -> str:
    s = occurred_at.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(ZoneInfo(time_zone)).strftime("%Y-%m-%d")


def ul_form(form: Optional[str]) -> str:
    """D4: a form that only restates the scope ("supplemental ...") means any form."""
    if not form or str(form).lower().startswith("supplemental"):
        return "any"
    return str(form)


_WS = re.compile(r"[\s\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def clean_text(value: Any, limit: int = 80) -> Optional[str]:
    """Display-safe text: no control characters or line breaks, single spaces, at most `limit` chars."""
    if value is None:
        return None
    s = _WS.sub(" ", str(value)).strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "\u2026"
    return s


def compound_rows(components: list[IntakeComponent], start: str, end: str) -> list[dict]:
    rows = []
    for r in compound_exposure_summary(components, start, end):
        rows.append({
            "substance": clean_text(r.substance),
            "form": clean_text(r.form),
            "unit": clean_text(r.unit),
            "total": None if r.total is None else fmt_decimal(r.total),
            "known_count": r.known_count,
            "unknown_count": r.unknown_count,
            "first_day": r.first_day,
            "last_day": r.last_day,
            "days": r.days,
            "sources": [clean_text(x) for x in r.sources],
        })
    return rows


def _links(conn: sqlite3.Connection) -> dict[str, tuple[str, str, str, str]]:
    """lower-case sample uuid -> (owner, producer, intake, component_id) of the active claim."""
    try:
        rows = conn.execute(_LINKS_SQL).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, tuple[str, str, str, str]] = {}
    for owner, producer, intake, comp, uuid in rows:
        out.setdefault(str(uuid).lower(), (owner, producer, intake, comp))
    return out


def _uuid_of(client_record_id: str) -> str:
    return client_record_id[-36:].lower()


def build_contributions(conn: sqlite3.Connection, components: list[IntakeComponent], dri: dict,
                        start: date, end: date, tz: tzinfo) -> list[Contribution]:
    by_apple = {row["apple"]: key for key, row in (dri.get("nutrients") or {}).items()
                if isinstance(row, dict) and row.get("apple") and row.get("ul") is not None}
    if not by_apple:
        return []
    unit_of = {k: dri["nutrients"][k].get("unit", "") for k in by_apple.values()}
    s_iso, e_iso = start.isoformat(), end.isoformat()
    comp_by_key = {(c.owner_id, c.producer_id, c.intake_id, c.component_id): c for c in components}
    links = _links(conn) if components else {}

    lo = (datetime(start.year, start.month, start.day, tzinfo=tz)).astimezone(ZoneInfo("UTC"))
    hi = (datetime(end.year, end.month, end.day, tzinfo=tz) + timedelta(days=1)).astimezone(ZoneInfo("UTC"))
    codes = sorted(by_apple)
    marks = ",".join("?" * len(codes))
    stored: set[str] = set()
    if links:
        stored = {_uuid_of(r[0]) for r in conn.execute(
            f"SELECT client_record_id FROM samples WHERE type_code IN ({marks})", codes)}
    rows = conn.execute(
        f"SELECT start_time, type_code, value, unit, client_record_id FROM samples "
        f"WHERE type_code IN ({marks}) AND start_time >= ? AND start_time < ? ORDER BY start_time",
        (*codes, lo.strftime("%Y-%m-%dT%H:%M:%SZ"), hi.strftime("%Y-%m-%dT%H:%M:%SZ")),
    ).fetchall()

    out: list[Contribution] = []
    for ts_str, tc, val, unit, crid in rows:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        day = ts.astimezone(tz).date().isoformat()
        owner = comp_by_key.get(links.get(_uuid_of(crid), ()))
        if owner is not None:
            out.append(Contribution(day, by_apple[tc], _dec(val), unit,
                                    "supplement" if owner.category == "supplement" else "food",
                                    owner.label_name or owner.quantity_basis))
        else:
            out.append(Contribution(day, by_apple[tc], _dec(val), unit, "unknown", None))

    for c in components:
        if c.kind != "nutrient" or c.code not in by_apple:
            continue
        if any(lk == (c.owner_id, c.producer_id, c.intake_id, c.component_id) and u in stored
               for u, lk in links.items()):
            continue  # its linked sample is stored: counted once, through the sample
        day = _local_day(c.occurred_at, c.time_zone)
        if day < s_iso or day > e_iso:
            continue
        amount = _dec(c.amount) if c.value_state == "known" else None
        out.append(Contribution(day, by_apple[c.code], amount, c.unit or "",
                                "supplement" if c.category == "supplement" else "food",
                                c.label_name or c.quantity_basis))
    return out


def upper_limits(conn: sqlite3.Connection, components: list[IntakeComponent], dri: dict,
                 start: date, end: date, tz: tzinfo) -> dict[str, dict]:
    try:
        contribs = build_contributions(conn, components, dri, start, end, tz)
    except sqlite3.OperationalError:
        return {}
    nuts = {}
    for key, row in (dri.get("nutrients") or {}).items():
        if isinstance(row, dict) and row.get("ul") is not None and row.get("apple"):
            nuts[key] = {"ul": _dec(row["ul"]), "ul_scope": row.get("ul_scope", "food+supplements"),
                         "form": ul_form(row.get("form")), "unit": row.get("unit", "")}
    out: dict[str, dict] = {}
    for f in compare_to_ul(contribs, nuts):
        out[f.nutrient] = _finding_json(f)
    return out


def _finding_json(f: ULFinding) -> dict:
    return {
        "status": f.status,
        "ul": None if f.ul is None else fmt_decimal(f.ul),
        "unit": f.unit,
        "ul_scope": f.ul_scope,
        "ul_form": f.ul_form,
        "high_days": list(f.high_days),
        "undetermined_days": list(f.undetermined_days),
        "mean_daily": None if f.mean_daily is None else fmt_decimal(f.mean_daily),
        "days_counted": f.days_counted,
    }


def intake_sections(conn: sqlite3.Connection, dri: dict, start: date, end: date, tz: tzinfo) -> tuple[list[dict], dict[str, dict]]:
    """Return (compounds, upper_limits) for the window; both empty without intake data."""
    has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intake_revisions'").fetchone()
    if not has_tables:
        return [], {}  # older receiver DB: no intake context at all
    components = read_effective_components(conn)
    return (compound_rows(components, start.isoformat(), end.isoformat()),
            upper_limits(conn, components, dri, start, end, tz))


def text_lines(compounds: list[dict], limits: dict[str, dict], dri: dict, start: date, end: date) -> list[str]:
    lines: list[str] = []
    if compounds:
        lines.append(f"Compounds from the intake context ({start.isoformat()} to {end.isoformat()}):")
        for r in compounds:
            name = clean_text(r["substance"]) + (f" ({clean_text(r['form'])})" if r["form"] else "")
            total = "amount unknown" if r["total"] is None else f"{r['total']} {clean_text(r['unit']) or ''}".rstrip()
            lines.append(f"  {name}: {total} total, {r['known_count']} known, {r['unknown_count']} unknown, "
                         f"on {r['days']} day{'s' if r['days'] != 1 else ''} ({r['first_day']} to {r['last_day']})")
    if limits:
        lines.append("Upper limits (UL) from food log and intake context:")
        for key, v in limits.items():
            label = dri["nutrients"][key].get("label", key)
            head = f"  {label}: {v['status']} (UL {v['ul']} {v['unit']}, {v['ul_scope']}"
            head += f", form {v['ul_form']})" if v["ul_form"] != "any" else ")"
            parts = [head]
            if v["high_days"]:
                parts.append("above on " + ", ".join(v["high_days"]))
            if v["mean_daily"] is not None:
                parts.append(f"mean {v['mean_daily']} {v['unit']} on {v['days_counted']} determined day"
                             f"{'s' if v['days_counted'] != 1 else ''}")
            if v["undetermined_days"]:
                parts.append("form, source or amount unknown on " + ", ".join(v["undetermined_days"]))
            lines.append("; ".join(parts))
    return lines
