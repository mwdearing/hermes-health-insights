"""Nutrition report: food-log intake vs Dietary Reference Intakes.

Reads a merged-history SQLite DB, sums dietary_* entries per Chicago day,
compares the window average with DRI targets from a YAML file, names
supplement candidates and what is not tracked.

Not medical advice. Aggregates only.
"""

from __future__ import annotations

import sqlite3
import yaml
from health_insights import energy as energy_mod
from health_insights.units import to_display_weight, weight_unit
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from health_insights import settings
from health_insights.sqlite_ro import connect_readonly

CHI = settings.timezone()


def _open_ro(db_path: str) -> sqlite3.Connection:
    return connect_readonly(db_path)


def _query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _load_dri(dri_path_or_yaml: str) -> dict:
    """Load DRI config from a file path or inline YAML string; the user's profile settings win."""
    if dri_path_or_yaml.strip().startswith("group:"):
        cfg = yaml.safe_load(dri_path_or_yaml)
    else:
        with open(dri_path_or_yaml, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    cfg.update(settings.profile())
    return cfg


def _chicago_to_utc(dt: datetime) -> datetime:
    """Convert a Chicago-aware datetime to UTC."""
    return dt.astimezone(ZoneInfo("UTC"))


def _daily_sums(db_path: str, start: date, end: date) -> dict[str, dict[str, float]]:
    """Return {date_str: {type_code: sum_value}} for dietary_* samples in [start, end].

    Groups by the Chicago date of start_time.  start_time in the DB is UTC,
    so we convert Chicago date boundaries to UTC before querying.
    """
    # Chicago midnight on start/end+1 → UTC
    start_chi = datetime(start.year, start.month, start.day, tzinfo=CHI)
    end_chi = datetime(end.year, end.month, end.day, tzinfo=CHI) + timedelta(days=1)
    start_utc = _chicago_to_utc(start_chi).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = _chicago_to_utc(end_chi).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = _open_ro(db_path)
    rows = _query(
        conn,
        """SELECT start_time, type_code, value FROM samples
           WHERE type_code LIKE 'dietary_%'
           AND start_time >= ? AND start_time < ?
           ORDER BY start_time""",
        (start_utc, end_utc),
    )
    conn.close()

    # Parse start_time to Chicago date, accumulate
    daily: dict[str, dict[str, float]] = {}
    for ts_str, tc, val in rows:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        chi_date = ts.astimezone(CHI).date().isoformat()
        if chi_date not in daily:
            daily[chi_date] = {}
        daily[chi_date][tc] = daily[chi_date].get(tc, 0.0) + val
    return daily


def _latest_weight(db_path: str, ref_date: date) -> Optional[float]:
    """Return the latest weight sample (kg) on or before ref_date."""
    conn = _open_ro(db_path)
    rows = _query(
        conn,
        """SELECT value FROM samples
           WHERE type_code = 'weight' AND start_time < ?
           ORDER BY start_time DESC LIMIT 1""",
        ((ref_date + timedelta(days=1)).isoformat(),),   # samples on ref_date itself count
    )
    conn.close()
    if rows and rows[0][0] is not None:
        return float(rows[0][0])
    return None


def _recent_weight(db_path: str, ref_date: date, days: int = 14) -> Optional[float]:
    """Latest weight (kg) on or before ref_date, only if it is at most `days` old."""
    conn = _open_ro(db_path)
    rows = _query(
        conn,
        """SELECT value FROM samples
           WHERE type_code = 'weight' AND start_time < ? AND start_time >= ?
           ORDER BY start_time DESC LIMIT 1""",
        ((ref_date + timedelta(days=1)).isoformat(), (ref_date - timedelta(days=days)).isoformat()),
    )
    conn.close()
    return float(rows[0][0]) if rows and rows[0][0] is not None else None


def _latest_height(db_path: str, ref_date: date) -> Optional[float]:
    """Return the latest height sample (cm) on or before ref_date."""
    conn = _open_ro(db_path)
    rows = _query(
        conn,
        """SELECT value FROM samples
           WHERE type_code = 'height' AND start_time < ?
           ORDER BY start_time DESC LIMIT 1""",
        ((ref_date + timedelta(days=1)).isoformat(),),   # samples on ref_date itself count
    )
    conn.close()
    if rows and rows[0][0] is not None:
        return float(rows[0][0])
    return None


_MIFFLIN_ACTIVITY = {"sedentary": 1.2, "low_active": 1.375, "active": 1.55, "very_active": 1.725}


def _estimated_need(dri: dict, weight_kg: float, height_cm: float) -> tuple[int, dict]:
    """Mifflin-St Jeor (1990) BMR x activity_factor.

    Returns (estimated_need_kcal, inputs_dict).
    inputs_dict has weight_kg, height_cm, age_years, activity_factor.
    """
    sex = dri.get("sex", "male")
    age_years = dri.get("age_years", 35)
    activity_factor = dri.get("activity_factor", 1.4)

    if dri.get("pal"):
        pal = energy_mod.pal_value(sex, dri["pal"])
        goal = int(dri.get("goal_kcal", 0))
        eer = energy_mod.eer_kcal(sex, age_years, weight_kg, height_cm / 100.0, pal)
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years + (5 if sex != "female" else -161)
        return round(eer) + goal, {
            "weight_kg": weight_kg, "height_cm": height_cm, "age_years": age_years,
            "activity_factor": activity_factor, "pal_band": dri["pal"], "pal": pal,
            "goal_kcal": goal, "eer_kcal": round(eer), "crosscheck_kcal": round(bmr * _MIFFLIN_ACTIVITY[dri["pal"]]),
        }

    if sex == "female":
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years - 161
    else:
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years + 5

    need = round(bmr * activity_factor)
    inputs = {
        "weight_kg": weight_kg,
        "height_cm": height_cm,
        "age_years": age_years,
        "activity_factor": activity_factor,
    }
    return need, inputs



def _tracked_keys(daily: dict[str, dict[str, float]], dri: dict) -> set[str]:
    """Nutrient keys whose Apple type has at least one row in the window (on any day)."""
    present = set()
    for values in daily.values():
        present.update(values.keys())
    return {k for k, nut in dri["nutrients"].items() if nut.get("apple") in present}


def _effective_target(nut: dict, weight_kg: Optional[float]) -> Optional[float]:
    """The RDA/AI to judge against: per_kg x body weight when both exist, else the fixed target."""
    per_kg = nut.get("per_kg")
    if per_kg is not None and weight_kg:
        return per_kg * weight_kg
    return nut.get("target")


def _low_counts(daily: dict, logged_days: list[str], dri: dict, weight_kg: Optional[float]) -> dict[str, int]:
    """{key: number of logged days whose intake is under low_share x target} for every tracked nutrient with a
    target. This is the per-day rule behind 'low on N of M logged days' and the supplement-candidate line
    (review 2026-09-23: the first build faked the count from the window average)."""
    low_share = dri.get("rules", {}).get("low_share", 0.70)
    out: dict[str, int] = {}
    for key, nut in dri["nutrients"].items():
        apple = nut.get("apple")
        target = _effective_target(nut, weight_kg)
        if not apple or not target or target <= 0 or nut.get("type") in ("limit", "amdr", "none"):
            continue
        out[key] = sum(1 for d in logged_days if daily.get(d, {}).get(apple, 0.0) < low_share * target)
    return out


def _dri_table(dri_arg: str, cfg: dict) -> dict:
    """Return {group, label, default} for the DRI table used in this report."""
    default = dri_arg == str(settings.package_data("dri.yaml"))
    group = cfg.get("group", "")
    if group.startswith("male "):
        age_range = group[5:]
        label = f"adult men {age_range.replace('-', ' to ')}" if age_range else group
    elif group.startswith("female "):
        age_range = group[7:]
        label = f"adult women {age_range.replace('-', ' to ')}" if age_range else group
    elif group:
        label = group
    else:
        label = "unspecified group"
    return {"group": group, "label": label, "default": default}


def _wrap(prefix: str, names: list[str], width: int = 160, sep: str = ", ") -> list[str]:
    """One line '<prefix>a, b, c' wrapped onto two-space-indented continuation lines, none over `width`."""
    lines: list[str] = []
    cur = prefix
    first = True
    for name in names:
        piece = name if first else sep + name
        if len(cur) + len(piece) > width and not first:
            lines.append(cur.rstrip(sep.strip() or ","))
            cur = "  " + name
        else:
            cur += piece
        first = False
    lines.append(cur)
    return lines


def _format_line(
    nutrient_key: str,
    info: dict,
    avg: float,
    unit: str,
    dri: dict,
    logged_days: int,
    partial_days: int,
    avg_energy: float = 0.0,
    days: int = 7,
    low_count: Optional[int] = None,
) -> str:
    """Build a properly formatted nutrient line ('{days}d avg ...'); `low_count` is the number of logged
    days under low_share x target (None when the nutrient has no target)."""
    nut = dri["nutrients"][nutrient_key]
    label = nut["label"]
    target = nut.get("target")
    ul = nut.get("ul")
    nut_type = nut.get("type", "none")
    energy_share_max = nut.get("energy_share_max")
    kcal_per_unit = nut.get("kcal_per_unit")
    per_kg = nut.get("per_kg")

    # ---- Saturated fat (energy share) ----
    if energy_share_max is not None:
        sat_kcal = avg * (kcal_per_unit or 9)
        share_pct = round(sat_kcal / avg_energy * 100) if avg_energy > 0 else 0
        limit_pct = round(energy_share_max * 100)
        return f"{label}: {days}d avg {avg:.0f} g = {share_pct}% of energy vs {limit_pct}% limit"

    # ---- Limit type (sodium) ----
    if nut_type == "limit" and target is not None:
        verb = "over" if avg > target else "within"
        return f"{label}: {days}d avg {avg:.0f} {unit}, {verb} the {target} {unit} limit"

    # ---- Standard nutrient (rda, ai, amdr, none) ----
    if target is not None:
        pct = round(avg / target * 100) if target > 0 else 0
        # Per-kg protein
        if per_kg is not None:
            weight = info.get("weight_kg")
            if weight is not None:
                derived_target = per_kg * weight
                derived_pct = round(avg / derived_target * 100) if derived_target > 0 else 0
                line = f"{label}: {days}d avg {avg:.0f} {unit} = {derived_pct}% of {derived_target:.0f} g ({per_kg} g per kg of body weight, {to_display_weight(weight):.0f} {weight_unit()})"
                if ul is not None and avg > ul:
                    line += f" - above UL {ul} {unit}"
                if low_count:
                    line += f", low on {low_count} of {logged_days} logged days"
                return line
            else:
                line = f"{label}: {days}d avg {avg:.0f} {unit} = {pct}% of {target} {unit}"
                if ul is not None and avg > ul:
                    line += f" - above UL {ul} {unit}"
                if low_count:
                    line += f", low on {low_count} of {logged_days} logged days"
                return line

        line = f"{label}: {days}d avg {avg:.0f} {unit} = {pct}% of {target} {unit}"
        if nut_type == "rda":
            line += " RDA"
        elif nut_type == "ai":
            line += " AI"

        if ul is not None and avg > ul:
            line += f" - above UL {ul} {unit}"

        if low_count:
            line += f", low on {low_count} of {logged_days} logged days"
        return line

    # No target
    return f"{label}: {days}d avg {avg:.0f} {unit}"


def _supplement_candidates(low_counts: dict[str, int], dri: dict, tracked: set[str], as_labels: bool = False) -> list[str]:
    """Vitamins/minerals that ARE tracked and are under low_share x target on at least low_days logged days.
    An untracked nutrient has no intake to judge: it belongs in the not-tracked line, never here."""
    rules = dri.get("rules", {})
    low_days = rules.get("low_days", 4)
    out: list[str] = []
    for kind in rules.get("supplement_kinds", ["vitamin", "mineral"]):
        for key in dri.get("kinds", {}).get(kind, []):
            if key not in tracked or key not in low_counts:
                continue
            if low_counts[key] >= low_days:
                out.append(dri["nutrients"][key].get("label", key) if as_labels else key)
    return sorted(out)


def _not_tracked(dri: dict, tracked: set[str], as_labels: bool = True) -> list[str]:
    """Vitamins/minerals with an Apple Health type that has NO rows in the window."""
    rules = dri.get("rules", {})
    out: list[str] = []
    for kind in rules.get("supplement_kinds", ["vitamin", "mineral"]):
        for key in dri.get("kinds", {}).get(kind, []):
            nut = dri["nutrients"].get(key, {})
            if nut.get("apple") is None or key in tracked:
                continue
            out.append(nut.get("label", key) if as_labels else key)
    return sorted(out)


def _labs_check(dri: dict, labs_db: Optional[str]) -> str:
    """Build the labs line from lab_results table.

    Uses the lab description text (e.g. "25-hydroxyvitamin D") for output,
    which is what the acceptance script checks for.
    """
    lab_nutrients: list[tuple[str, str]] = []
    for key, nut in dri["nutrients"].items():
        lab = nut.get("lab")
        if lab:
            lab_nutrients.append((nut["label"], lab))

    if not lab_nutrients:
        return "Labs: none"

    on_file: list[str] = []
    not_on_file: list[str] = []

    if labs_db:
        conn = _open_ro(labs_db)
        for label, lab_desc in lab_nutrients:
            # Extract LOINC code from the lab description (e.g. "25-hydroxyvitamin D (LOINC 1989-3)")
            loinc = lab_desc.split("LOINC ")[-1].rstrip(")") if "LOINC " in lab_desc else None
            # Extract the display name (text before the parenthesized LOINC)
            display = lab_desc.split(" (LOINC")[0].strip() if " (LOINC" in lab_desc else label
            if loinc:
                rows = _query(
                    conn,
                    "SELECT effective_date FROM lab_results WHERE loinc = ? ORDER BY effective_date DESC LIMIT 1",
                    (loinc,),
                )
                if rows:
                    on_file.append(f"{display} on file {rows[0][0]}")
                else:
                    not_on_file.append(f"{display} not on file")
            else:
                not_on_file.append(f"{display} not on file")
        conn.close()
    else:
        for label, lab_desc in lab_nutrients:
            display = lab_desc.split(" (LOINC")[0].strip() if " (LOINC" in lab_desc else label
            not_on_file.append(f"{display} not on file")

    parts = on_file + not_on_file
    return "Labs: " + "; ".join(parts)


def report(
    db_path: str,
    dri: str,
    ref_date: str,
    days: int = 7,
    labs_db: Optional[str] = None,
) -> list[str]:
    """Return the nutrition report as a list of lines."""
    config = _load_dri(dri)
    dri_cfg = config
    rules = dri_cfg.get("rules", {})
    min_logged = rules.get("min_logged_days", 4)
    partial_kcal = rules.get("partial_day_kcal", 1200)

    ref = date.fromisoformat(ref_date)
    start = ref - timedelta(days=days - 1)
    end = ref

    # Get daily sums
    daily = _daily_sums(db_path, start, end)

    # Identify logged days (have dietary_energy_consumed)
    logged_days_list: list[str] = []
    partial_days_list: list[str] = []
    for day_str, values in sorted(daily.items()):
        energy = values.get("dietary_energy_consumed", 0)
        if energy > 0:
            logged_days_list.append(day_str)
            if energy < partial_kcal:
                partial_days_list.append(day_str)

    days_logged = len(logged_days_list)
    days_partial = len(partial_days_list)

    # Check for empty db
    if days_logged == 0:
        return [
            f"No food log entries for {start.isoformat()} to {end.isoformat()} ({settings.tz_label()}).",
            "Informational, not medical advice.",
        ]

    # Check for too few days
    if days_logged < min_logged:
        return [
            f"Not enough food-log days ({days_logged} of {days}) for {start.isoformat()} to {end.isoformat()} ({settings.tz_label()}).",
            "Informational, not medical advice.",
        ]

    # Compute window averages
    nutrient_avgs: dict[str, float] = {}
    for tc in dri_cfg["nutrients"]:
        apple = dri_cfg["nutrients"][tc].get("apple")
        if not apple:
            continue
        total = sum(daily[d].get(apple, 0) for d in logged_days_list)
        nutrient_avgs[tc] = total / days_logged

    # Get weight for protein calculation
    weight_kg = _latest_weight(db_path, ref)
    info: dict = {"weight_kg": weight_kg}
    tracked = _tracked_keys(daily, dri_cfg)
    low_counts = _low_counts(daily, logged_days_list, dri_cfg, weight_kg)

    # Compute average energy for saturated fat
    total_energy = sum(
        daily[d].get("dietary_energy_consumed", 0) for d in logged_days_list
    )
    avg_energy = total_energy / days_logged if days_logged > 0 else 0

    # Build header
    header = (
        f"Nutrition {start.isoformat()} to {end.isoformat()} ({settings.tz_label()}): "
        f"{days_logged} of {days} days logged"
    )
    if days_partial > 0:
        header += f", {days_partial} partial"

    lines: list[str] = [header]

    # DRI table label (after header, before any nutrient lines)
    dri_info = _dri_table(dri, dri_cfg)
    if dri_info["default"]:
        lines.append(f"Reference: Dietary Reference Intakes for {dri_info['label']} (default table; use --dri for others)")
    else:
        lines.append(f"Reference: Dietary Reference Intakes for {dri_info['label']} (custom table from --dri)")

    # ---- Energy line (IOM EER target when `pal` is configured, else Mifflin-St Jeor) ----
    height_cm = _latest_height(db_path, ref)
    energy_weight = _recent_weight(db_path, ref)
    has_height = height_cm is not None
    has_weight = energy_weight is not None
    has_profile = bool(dri_cfg.get("age_years")) and bool(dri_cfg.get("sex"))
    if has_height and has_weight and has_profile:
        need_kcal, energy_inputs = _estimated_need(dri_cfg, energy_weight, height_cm)
        pct = round(avg_energy / need_kcal * 100) if need_kcal > 0 else 0
        body = f"{to_display_weight(energy_weight):.0f} {weight_unit()}, {height_cm:.0f} cm, age {dri_cfg.get('age_years', '??')}"
        if "pal" in energy_inputs:
            goal = energy_inputs["goal_kcal"]
            goal_txt = f", goal {goal:+d} kcal" if goal else ""
            what = (f"{need_kcal} kcal target (IOM EER {energy_inputs['eer_kcal']}, "
                    f"{energy_inputs['pal_band'].replace('_', ' ')}{goal_txt}; {body})")
            if abs(energy_inputs["crosscheck_kcal"] - energy_inputs["eer_kcal"]) > 0.10 * energy_inputs["eer_kcal"]:
                what += f", Mifflin x PAL cross-check {energy_inputs['crosscheck_kcal']}"
        else:
            what = f"{need_kcal} kcal estimated need (Mifflin-St Jeor {body}, activity {dri_cfg.get('activity_factor', '??')})"
        e_line = f"Energy: {days}d avg {avg_energy:.0f} kcal on logged days = {pct}% of {what}"
        if pct < 70:
            e_line += ", probably under-logging"
        lines.append(e_line)
    else:
        missing = "height" if not has_height else "weight in the last 14 days" if not has_weight else "profile: set sex and age_years under profile in the config file"
        lines.append(f"Energy: {days}d avg {avg_energy:.0f} kcal on logged days, estimate n/a (no {missing})")

    # Report each nutrient in order (only those with a DRI target/ul/energy_share_max)
    for key, nut in dri_cfg["nutrients"].items():
        apple = nut.get("apple")
        if not apple:
            continue
        if nut.get("target") is None and nut.get("ul") is None and nut.get("energy_share_max") is None:
            continue  # no DRI comparison possible (e.g. total fat, cholesterol)
        avg = nutrient_avgs.get(key, 0)
        if avg <= 0:
            continue
        unit = nut.get("unit", "")
        line = _format_line(
            key, info, avg, unit, dri_cfg, days_logged, days_partial, avg_energy,
            days=days, low_count=low_counts.get(key),
        )
        lines.append(line)

    # Consistently below target (tracked vitamins/minerals low on low_days+ days)
    candidates = _supplement_candidates(low_counts, dri_cfg, tracked, as_labels=True)
    lines.extend(_wrap("Consistently below target: ", candidates or ["none"]))
    if candidates:
        lines.append("  (worth discussing with a clinician or dietitian)")

    # Not tracked (an Apple type with no rows in the window)
    not_tracked = _not_tracked(dri_cfg, tracked)
    if not_tracked:
        lines.extend(_wrap("Not tracked by the food log: ", not_tracked))

    # Labs
    labs_line = _labs_check(dri_cfg, labs_db)
    lines.extend(_wrap("Labs: ", [x.strip() for x in labs_line[len("Labs: "):].split(";")], sep="; ") if labs_line.startswith("Labs: ") else [labs_line])

    # Footer
    lines.append("Informational, not medical advice.")

    return lines


def report_json(
    db_path: str,
    dri: str,
    ref_date: str,
    days: int = 7,
    labs_db: Optional[str] = None,
) -> dict:
    """Return the nutrition report as a dict."""
    config = _load_dri(dri)
    dri_cfg = config
    rules = dri_cfg.get("rules", {})
    min_logged = rules.get("min_logged_days", 4)
    partial_kcal = rules.get("partial_day_kcal", 1200)

    ref = date.fromisoformat(ref_date)
    start = ref - timedelta(days=days - 1)
    end = ref

    daily = _daily_sums(db_path, start, end)

    logged_days_list: list[str] = []
    partial_days_list: list[str] = []
    for day_str, values in sorted(daily.items()):
        energy = values.get("dietary_energy_consumed", 0)
        if energy > 0:
            logged_days_list.append(day_str)
            if energy < partial_kcal:
                partial_days_list.append(day_str)

    days_logged = len(logged_days_list)
    days_partial = len(partial_days_list)

    if days_logged == 0:
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days_logged": 0,
            "days_partial": 0,
            "error": "no food log entries",
        }

    # Compute window averages
    nutrient_avgs: dict[str, float] = {}
    for tc in dri_cfg["nutrients"]:
        apple = dri_cfg["nutrients"][tc].get("apple")
        if not apple:
            continue
        total = sum(daily[d].get(apple, 0) for d in logged_days_list)
        nutrient_avgs[tc] = total / days_logged

    weight_kg = _latest_weight(db_path, ref)
    info: dict = {"weight_kg": weight_kg}

    total_energy = sum(
        daily[d].get("dietary_energy_consumed", 0) for d in logged_days_list
    )
    avg_energy = total_energy / days_logged if days_logged > 0 else 0

    # Build nutrients dict
    nutrients: dict[str, dict] = {}
    for key, nut in dri_cfg["nutrients"].items():
        apple = nut.get("apple")
        if not apple:
            continue
        avg = nutrient_avgs.get(key, 0)
        unit = nut.get("unit", "")
        target = nut.get("target")
        pct = 0
        if target is not None and target > 0:
            if nut.get("per_kg") and weight_kg:
                derived = nut["per_kg"] * weight_kg
                pct = round(avg / derived * 100)
            else:
                pct = round(avg / target * 100)

        # Real bug found 2026-09-28 (rendering the new nutrition card made it visible: Sodium at
        # 130% of its 2300 mg limit showed a green "ok" bar). For a "limit"-type nutrient (sodium
        # here; report()'s text line already treats it specially), `target` IS the ceiling, not a
        # floor -- avg > target is the bad direction, the opposite of every other nutrient type.
        status = "ok"
        ul = nut.get("ul")
        nut_type = nut.get("type", "none")
        if nut_type == "limit" and target is not None and target > 0:
            status = "above_ul" if avg > target else "ok"
        elif ul is not None and avg > ul:
            status = "above_ul"
        elif target is not None and target > 0 and pct < 70:
            status = "low"

        nutrients[key] = {
            "avg": round(avg, 2),
            "unit": unit,
            "target": target,
            "pct": pct,
            "status": status,
        }

    tracked = _tracked_keys(daily, dri_cfg)
    low_counts = _low_counts(daily, logged_days_list, dri_cfg, weight_kg)
    candidates = _supplement_candidates(low_counts, dri_cfg, tracked)
    not_tracked = _not_tracked(dri_cfg, tracked, as_labels=False)

    # ---- Energy block ----
    height_cm = _latest_height(db_path, ref)
    energy_weight = _recent_weight(db_path, ref)
    has_height = height_cm is not None
    has_weight = energy_weight is not None
    has_profile = bool(dri_cfg.get("age_years")) and bool(dri_cfg.get("sex"))
    method = "iom_eer" if dri_cfg.get("pal") else "mifflin_st_jeor"
    energy: dict[str, Any] = {"method": method, "avg_kcal": avg_energy}
    if has_height and has_weight and has_profile:
        need_kcal, energy_inputs = _estimated_need(dri_cfg, energy_weight, height_cm)
        pct = round(avg_energy / need_kcal * 100) if need_kcal > 0 else 0
        energy["estimated_need_kcal"] = need_kcal
        energy["pct"] = pct
        energy["inputs"] = energy_inputs
    else:
        energy["estimated_need_kcal"] = None
        energy["pct"] = None
        energy["inputs"] = {
            "weight_kg": energy_weight,
            "height_cm": height_cm,
            "age_years": dri_cfg.get("age_years"),
            "activity_factor": dri_cfg.get("activity_factor"),
        }

    # Labs
    labs_info: list[str] = []
    for key, nut in dri_cfg["nutrients"].items():
        lab = nut.get("lab")
        if lab:
            label = nut["label"]
            loinc = lab.split("LOINC ")[-1].rstrip(")") if "LOINC " in lab else None
            if not labs_db or not loinc:
                labs_info.append(f"{label}: not on file")
            else:
                conn = _open_ro(labs_db)
                rows = _query(
                    conn,
                    "SELECT effective_date FROM lab_results WHERE loinc = ? ORDER BY effective_date DESC LIMIT 1",
                    (loinc,),
                )
                conn.close()
                if rows:
                    labs_info.append(f"{label}: on file {rows[0][0]}")
                else:
                    labs_info.append(f"{label}: not on file")

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days_logged": days_logged,
        "days_partial": days_partial,
        "dri_table": _dri_table(dri, dri_cfg),
        "nutrients": nutrients,
        "supplement_candidates": candidates,
        "not_tracked": not_tracked,
        "labs": labs_info,
        "energy": energy,
    }
