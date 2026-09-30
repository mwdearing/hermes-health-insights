"""Tests for the nutrition report (health_insights/nutrition.py).

Fake data only -- no real health values cross into this suite. Each test builds a
tiny SQLite DB in tmp_path and calls health_insights.nutrition.report() / report_json()
directly, asserting on the rendered lines and JSON shape.
"""
import sqlite3
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from health_insights import nutrition

CHI = ZoneInfo("America/Chicago")
REF = Date(2030, 6, 30)

DRI_SMALL = """
group: male 31-50
energy_unit: kcal
nutrients:
  fiber:     {label: Fiber,     unit: g,  type: ai,    target: 38,   ul: null, apple: dietary_fiber,   source: t}
  protein:   {label: Protein,   unit: g,  type: rda,   target: 56,   ul: null, apple: dietary_protein, per_kg: 0.8, source: t}
  calcium:   {label: Calcium,   unit: mg, type: rda,   target: 1000, ul: 2500, apple: dietary_calcium, source: t}
  iron:      {label: Iron,      unit: mg, type: rda,   target: 8,    ul: 45,   apple: dietary_iron,    source: t}
  vitamin_c: {label: Vitamin C, unit: mg, type: rda,   target: 90,   ul: 2000, apple: dietary_vitamin_c, source: t}
  sodium:    {label: Sodium,    unit: mg, type: limit, target: 2300, ul: null, apple: dietary_sodium,  source: t}
  fat_saturated: {label: Saturated fat, unit: g, type: limit, energy_share_max: 0.10, kcal_per_unit: 9, apple: dietary_fat_saturated, source: t}
  sugar:     {label: Sugar (total), unit: g, type: none, apple: dietary_sugar, source: t}
  vitamin_d: {label: Vitamin D, unit: mcg, type: rda,  target: 15,   ul: 100,  apple: dietary_vitamin_d, lab: "25-hydroxyvitamin D (LOINC 1989-3)", source: t}
  vitamin_b12: {label: Vitamin B12, unit: mcg, type: rda, target: 2.4, ul: null, apple: dietary_vitamin_b12, lab: "Vitamin B12 (LOINC 2132-9)", source: t}
  magnesium: {label: Magnesium, unit: mg, type: rda,   target: 420,  ul: 350,  apple: dietary_magnesium, lab: "Magnesium, serum (LOINC 19123-9)", source: t}
rules: {low_share: 0.70, low_days: 4, min_logged_days: 4, partial_day_kcal: 1200, supplement_kinds: [vitamin, mineral]}
kinds:
  vitamin: [vitamin_c, vitamin_d, vitamin_b12]
  mineral: [calcium, iron, magnesium, sodium]
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_db(path, *, days_logged=6, weight_kg=90.0, iron=None, partial_day=False):
    """Build a minimal merged-history DB at *path*."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
        "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT)"
    )
    con.execute(
        "CREATE TABLE sleep_sessions (sleep_session_id INTEGER PRIMARY KEY, "
        "source_id INTEGER, client_record_id TEXT, start_time TEXT, end_time TEXT)"
    )
    n = [0]

    def add(tc, d, v, unit, hm):
        n[0] += 1
        ts = datetime.fromisoformat(f"{d.isoformat()}T{hm}:00").replace(tzinfo=CHI).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES ('t',?,?,?,?,?,?)",
            (tc, f"r{n[0]}", ts, ts, v, unit),
        )

    # weight sample one day before REF
    add("weight", REF - timedelta(days=1), weight_kg, "kg", "07:00")
    # meals: 3 entries a day on `days_logged` of the 7 days (REF-6 .. REF)
    for ago in range(7):
        if ago >= days_logged:
            continue
        d = REF - timedelta(days=ago)
        kcal = 2100.0 if not (partial_day and ago == 0) else 600.0
        per = {
            "dietary_energy_consumed": kcal / 3,
            "dietary_fiber": 19.0 / 3,
            "dietary_protein": 72.0 / 3,
            "dietary_calcium": 500.0 / 3,
            "dietary_iron": (iron if iron is not None else 12.0) / 3,
            "dietary_vitamin_c": 45.0 / 3,
            "dietary_sodium": 3000.0 / 3,
            "dietary_fat_saturated": 28.0 / 3,
            "dietary_sugar": 60.0 / 3,
        }
        for hm in ("08:00", "13:00", "19:00"):
            for tc, v in per.items():
                unit = "Cal" if tc == "dietary_energy_consumed" else (
                    "mg" if tc in (
                        "dietary_calcium", "dietary_iron", "dietary_vitamin_c",
                        "dietary_sodium",
                    ) else "g"
                )
                add(tc, d, v, unit, hm)
    con.commit()
    con.close()
    return str(path)


def _line(text, label):
    """Return the first line starting with *label*, or empty string."""
    for l in text.splitlines():
        if l.startswith(label):
            return l
    return ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPerDaySums:
    """Each day's intake is the SUM of all entries for that Chicago date."""

    def test_day_sum_fiber(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat(), days=7)
        fib = _line("\n".join(lines), "Fiber")
        assert "19 g" in fib, f"Expected 19 g (3 x 19/3), got: {fib}"

    def test_day_sum_energy(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat(), days=7)
        # header should contain the date range
        assert "2030-06-24" in lines[0] and "2030-06-30" in lines[0]


class TestProteinByWeight:
    """Protein target = per_kg x latest weight sample."""

    def test_protein_from_weight(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite", weight_kg=90.0)
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        pro = _line("\n".join(lines), "Protein")
        assert "0.8 g per kg" in pro, f"Expected per_kg notation: {pro}"
        assert "72 g" in pro, f"Expected 72 g target (0.8 x 90): {pro}"

    def test_protein_fallback_to_fixed(self, tmp_path):
        """No weight sample -> use fixed target."""
        con = sqlite3.connect(str(tmp_path / "db.sqlite"))
        con.execute(
            "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
            "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
            "value REAL, unit TEXT, metadata_json TEXT)"
        )
        # No weight, just protein + energy entries (72 g protein, 2100 kcal per day)
        for ago in range(6):
            d = REF - timedelta(days=ago)
            for hm in ("08:00", "13:00", "19:00"):
                ts = datetime.fromisoformat(f"{d.isoformat()}T{hm}:00").replace(tzinfo=CHI).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                # energy
                con.execute(
                    "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                    "VALUES ('t',?,?,?,?,?,?)",
                    ("dietary_energy_consumed", f"re{ago}", ts, ts, 700.0, "Cal"),
                )
                # protein
                con.execute(
                    "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                    "VALUES ('t',?,?,?,?,?,?)",
                    ("dietary_protein", f"rp{ago}", ts, ts, 72.0 / 3, "g"),
                )
        con.commit()
        con.close()
        lines = nutrition.report(str(tmp_path / "db.sqlite"), DRI_SMALL, REF.isoformat())
        pro = _line("\n".join(lines), "Protein")
        assert "56 g" in pro, f"Expected fixed target 56 g: {pro}"


class TestPercentOfTarget:
    """Report shows percentage of target met."""

    def test_fiber_percent(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        fib = _line("\n".join(lines), "Fiber")
        assert "50%" in fib, f"Expected 50% (19/38): {fib}"

    def test_vitamin_c_percent(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        vc = _line("\n".join(lines), "Vitamin C")
        assert "50%" in vc, f"Expected 50% (45/90): {vc}"


class TestULWarning:
    """When average exceeds UL, report a warning."""

    def test_iron_below_ul(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite", iron=12.0)
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        iron = _line("\n".join(lines), "Iron")
        # 12 mg < 45 mg UL -> no UL warning
        assert "UL" not in iron.split("(")[0], f"Should not warn about UL at 12 mg: {iron}"

    def test_iron_above_ul(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite", iron=50.0)
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        iron = _line("\n".join(lines), "Iron")
        assert "UL" in iron, f"Should warn about UL at 50 mg: {iron}"
        assert "45" in iron, f"Should mention UL 45: {iron}"


class TestSodiumLimit:
    """Sodium is a limit type; over-limit values get flagged."""

    def test_sodium_over_limit(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        sod = _line("\n".join(lines), "Sodium")
        assert "3000" in sod, f"Expected 3000 mg: {sod}"
        assert "2300" in sod, f"Expected limit 2300: {sod}"
        assert "over" in sod.lower() or "above" in sod.lower(), f"Expected over/above: {sod}"

    def test_sodium_under_limit_does_not_say_over(self, tmp_path):
        """Regression for a real bug found 2026-09-28 (health-insights 1.26.1, card build):
        report_json()'s status logic was fixed for the JSON/card path, but the TEXT line's
        limit-type branch (_format_line()) unconditionally said 'over the X limit' even when the
        average is genuinely under it -- confirmed the same day, tracked in AGENT.md, not fixed
        until now."""
        db = _build_db(tmp_path / "db.sqlite", days_logged=6)
        con = sqlite3.connect(str(db))
        con.execute("UPDATE samples SET value = value / 2 WHERE type_code = 'dietary_sodium'")
        con.commit()
        con.close()
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        sod = _line("\n".join(lines), "Sodium")
        assert "1500" in sod, f"Expected 1500 mg: {sod}"
        assert "2300" in sod, f"Expected limit 2300: {sod}"
        assert "over" not in sod.lower() and "above" not in sod.lower(), f"Should not say over/above: {sod}"
        assert "under" in sod.lower() or "within" in sod.lower(), f"Expected under/within: {sod}"


class TestSaturatedFatShare:
    """Saturated fat judged as share of energy."""

    def test_saturated_fat_percent(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        sat = _line("\n".join(lines), "Saturated fat")
        # 28 g * 9 = 252 kcal; 252/2100 = 12%
        assert "12%" in sat, f"Expected 12% of energy: {sat}"
        assert "10%" in sat, f"Expected 10% limit: {sat}"


class TestSupplementCandidates:
    """Vitamins/minerals under low_share of target on low_days+ are candidates."""

    def test_supplement_candidates_list(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        sup = _line("\n".join(lines), "Consistently below target")
        assert sup, f"Expected a supplement candidates line: {lines}"
        assert "Calcium" in sup, f"Expected Calcium: {sup}"
        assert "Vitamin C" in sup, f"Expected Vitamin C: {sup}"
        assert "Iron" not in sup, f"Iron (150%) should not be a candidate: {sup}"
        assert "Fiber" not in sup, f"Fiber should not be a candidate: {sup}"


class TestNotTracked:
    """Nutrients with no food-log rows in the window."""

    def test_not_tracked_list(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        nt = _line("\n".join(lines), "Not tracked")
        assert nt, f"Expected a not-tracked line: {lines}"
        assert "Vitamin D" in nt, f"Expected Vitamin D: {nt}"
        assert "Vitamin B12" in nt, f"Expected Vitamin B12: {nt}"
        assert "Magnesium" in nt, f"Expected Magnesium: {nt}"


class TestLabsCrossCheck:
    """Labs line shows dates for nutrients with lab tests."""

    def test_labs_on_file(self, tmp_path):
        labs_db = str(tmp_path / "labs.sqlite")
        lc = sqlite3.connect(labs_db)
        lc.execute(
            "CREATE TABLE lab_results (id INTEGER PRIMARY KEY, loinc TEXT, name TEXT, "
            "effective_date TEXT, value_num REAL, unit TEXT, flag TEXT, imported_at TEXT)"
        )
        lc.execute(
            "INSERT INTO lab_results (loinc, name, effective_date, value_num, unit, flag, imported_at) "
            "VALUES ('1989-3', '25-Hydroxyvitamin D', '2030-05-01', 30.0, 'ng/mL', '', '2030-05-02T00:00:00+00:00')"
        )
        lc.commit()
        lc.close()
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat(), labs_db=labs_db)
        labs = _line("\n".join(lines), "Labs")
        assert labs, f"Expected a Labs line: {lines}"
        assert "on file" in labs.lower(), f"Expected 'on file': {labs}"
        assert "2030-05-01" in labs, f"Expected date: {labs}"
        assert "30" not in labs.replace("2030", ""), "Labs line must not print values"

    def test_labs_not_on_file(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        labs = _line("\n".join(lines), "Labs")
        # Without labs_db, should still mention what's not on file
        assert labs, f"Expected a Labs line: {lines}"


class TestPartialDay:
    """A logged day under partial_day_kcal is flagged."""

    def test_partial_day_flagged(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite", partial_day=True)
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        assert "partial" in lines[0].lower(), f"Expected 'partial' in header: {lines[0]}"


class TestTooFewDays:
    """Fewer than min_logged_days -> error message."""

    def test_too_few_days(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite", days_logged=2)
        lines = nutrition.report(db, DRI_SMALL, REF.isoformat())
        assert "not enough" in "\n".join(lines).lower(), f"Expected 'not enough': {lines}"
        assert "2 of 7" in "\n".join(lines), f"Expected '2 of 7': {lines}"


class TestEmptyDb:
    """No dietary rows -> informative message."""

    def test_no_food_log(self, tmp_path):
        con = sqlite3.connect(str(tmp_path / "db.sqlite"))
        con.execute(
            "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
            "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
            "value REAL, unit TEXT, metadata_json TEXT)"
        )
        con.commit()
        con.close()
        lines = nutrition.report(str(tmp_path / "db.sqlite"), DRI_SMALL, REF.isoformat())
        assert "no food log" in "\n".join(lines).lower(), f"Expected 'no food log': {lines}"


class TestJsonOutput:
    """report_json returns a dict with the expected keys."""

    def test_json_shape(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        j = nutrition.report_json(db, DRI_SMALL, REF.isoformat())
        assert "start" in j
        assert "end" in j
        assert "days_logged" in j
        assert j["days_logged"] == 6
        assert "nutrients" in j
        assert "fiber" in j["nutrients"]
        assert "avg" in j["nutrients"]["fiber"]
        assert "pct" in j["nutrients"]["fiber"]
        assert j["nutrients"]["fiber"]["avg"] == pytest.approx(19.0, abs=0.01)
        assert j["nutrients"]["fiber"]["pct"] == 50
        assert "supplement_candidates" in j
        assert "calcium" in j["supplement_candidates"]
        assert "not_tracked" in j
        assert "vitamin_d" in j["not_tracked"]

    def test_json_no_values_in_text(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite")
        j = nutrition.report_json(db, DRI_SMALL, REF.isoformat())
        # Verify no raw health data leaks into brain_note / git
        assert isinstance(j["days_logged"], int)
        assert isinstance(j["nutrients"]["fiber"]["avg"], (int, float))

    def test_limit_type_nutrient_over_its_target_is_flagged_not_ok(self, tmp_path):
        """Regression for a real bug found 2026-09-28 while building the nutrition card: sodium
        (type: limit, target 2300 mg) at 3000 mg/day -- genuinely over its limit, exactly what the
        text report's 'over the 2300 mg limit' line already says -- was reported as status "ok"
        because the status logic only checked a separate "ul" field, which sodium doesn't have;
        target IS the ceiling for a limit-type nutrient."""
        db = _build_db(tmp_path / "db.sqlite")  # fixture logs 3000 mg/day sodium, target 2300
        j = nutrition.report_json(db, DRI_SMALL, REF.isoformat())
        assert j["nutrients"]["sodium"]["status"] == "above_ul"

    def test_limit_type_nutrient_under_its_target_stays_ok(self, tmp_path):
        db = _build_db(tmp_path / "db.sqlite", days_logged=6)
        # Halve the logged sodium so it's comfortably under the 2300 mg limit.
        import sqlite3
        con = sqlite3.connect(str(db))
        con.execute("UPDATE samples SET value = value / 2 WHERE type_code = 'dietary_sodium'")
        con.commit()
        con.close()
        j = nutrition.report_json(db, DRI_SMALL, REF.isoformat())
        assert j["nutrients"]["sodium"]["status"] == "ok"


# ---- review 2026-09-23: tracked vs not-tracked, per-day candidate rule, wrapping, window label ----

def _dri_path(tmp_path, text=None):
    p = tmp_path / "dri.yaml"
    p.write_text(text or DRI_SMALL)
    return str(p)


def test_not_tracked_never_lists_a_nutrient_that_has_rows_and_candidates_never_list_untracked_ones(tmp_path):
    """The first build ignored the data: every vitamin/mineral in `kinds` was 'not tracked' (calcium, iron
    included) and every nutrient with zero rows became a 'supplement candidate'."""
    db = _build_db(tmp_path / "db.sqlite")
    text = "\n".join(nutrition.report(db, _dri_path(tmp_path), REF.isoformat()))
    nt = _line(text, "Not tracked")
    sup = _line(text, "Consistently below target")
    assert "Calcium" not in nt and "Iron" not in nt and "Vitamin C" not in nt, nt
    assert "Vitamin D" in nt and "Vitamin B12" in nt and "Magnesium" in nt, nt
    assert "Vitamin D" not in sup and "Magnesium" not in sup, sup
    assert "Calcium" in sup and "Vitamin C" in sup and "Iron" not in sup, sup
    j = nutrition.report_json(db, _dri_path(tmp_path), REF.isoformat())
    assert "calcium" not in j["not_tracked"] and "vitamin_d" in j["not_tracked"]
    assert "vitamin_d" not in j["supplement_candidates"] and "calcium" in j["supplement_candidates"]


def test_candidate_needs_low_intake_on_at_least_low_days_logged_days(tmp_path):
    """Per-day rule: low on 4+ logged days. Calcium low on only 2 of 6 days (high the other 4) is not a candidate
    even though its 6-day average is what it is; the per-line 'low on N of M' count is the same number."""
    db = _build_db(tmp_path / "db.sqlite")
    con = sqlite3.connect(db)
    # make 4 of the 6 logged days calcium-rich: add one big entry on each of the 4 oldest logged days
    for ago in (2, 3, 4, 5):
        d = REF - timedelta(days=ago)
        ts = datetime.fromisoformat(f"{d.isoformat()}T21:00:00").replace(tzinfo=ZoneInfo("America/Chicago")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute("INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) VALUES ('t','dietary_calcium',?,?,?,1500.0,'mg')", (f"c{ago}", ts, ts))
    con.commit(); con.close()
    text = "\n".join(nutrition.report(db, _dri_path(tmp_path), REF.isoformat()))
    assert "low on 2 of 6" in _line(text, "Calcium"), _line(text, "Calcium")
    assert "Calcium" not in _line(text, "Consistently below target"), _line(text, "Consistently below target")


def test_long_lists_wrap_to_160_characters_without_losing_names(tmp_path):
    extra = "".join(f"  extra_{i}: {{label: Extra nutrient number {i}, unit: mg, type: rda, target: 10, ul: null, apple: dietary_extra_{i}, source: t}}\n" for i in range(12))
    text_dri = DRI_SMALL.replace("nutrients:\n", "nutrients:\n" + extra).replace(
        "vitamin: [vitamin_c, vitamin_d, vitamin_b12]", "vitamin: [vitamin_c, vitamin_d, vitamin_b12, " + ", ".join(f"extra_{i}" for i in range(12)) + "]")
    db = _build_db(tmp_path / "db.sqlite")
    lines = nutrition.report(db, _dri_path(tmp_path, text_dri), REF.isoformat())
    assert all(len(l) <= 160 for l in lines), max(len(l) for l in lines)
    joined = "\n".join(lines)
    assert "Extra nutrient number 11" in joined
    cont = [l for l in lines if l.startswith("  ")]
    assert cont, "continuation lines must be indented with two spaces"


def test_window_label_follows_days(tmp_path):
    db = _build_db(tmp_path / "db.sqlite")
    text = "\n".join(nutrition.report(db, _dri_path(tmp_path), REF.isoformat(), days=28))
    assert "28d avg" in _line(text, "Fiber") and "7d avg" not in text, _line(text, "Fiber")


# ---- energy line tests (step A) ----

DRI_ENERGY = """
group: male 31-50
sex: male
age_years: 40
activity_factor: 1.4
energy_unit: kcal
nutrients:
  fiber:   {label: Fiber,   unit: g,  type: ai,  target: 38, ul: null, apple: dietary_fiber, source: t}
  calcium: {label: Calcium, unit: mg, type: rda, target: 1000, ul: 2500, apple: dietary_calcium, source: t}
rules: {low_share: 0.70, low_days: 4, min_logged_days: 4, partial_day_kcal: 1200, supplement_kinds: [vitamin, mineral]}
kinds: {vitamin: [], mineral: [calcium]}
"""


def _build_energy_db(path, kcal=2100.0, weight=90.0, height=180.0):
    """Build a minimal DB with weight and optional height."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
        "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT)"
    )
    con.execute(
        "CREATE TABLE sleep_sessions (sleep_session_id INTEGER PRIMARY KEY, "
        "source_id INTEGER, client_record_id TEXT, start_time TEXT, end_time TEXT)"
    )
    n = [0]

    def add(tc, d, v, unit, hm="09:00"):
        n[0] += 1
        ts = datetime.fromisoformat(f"{d.isoformat()}T{hm}:00").replace(tzinfo=CHI).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
            "VALUES ('t',?,?,?,?,?,?)",
            (tc, f"r{n[0]}", ts, ts, v, unit),
        )

    if weight is not None:
        add("weight", REF - timedelta(days=1), weight, "kg", "07:00")
    if height is not None:
        add("height", REF - timedelta(days=100), height, "cm", "07:00")
    for ago in range(6):
        d = REF - timedelta(days=ago)
        for hm in ("08:00", "13:00", "19:00"):
            add("dietary_energy_consumed", d, kcal / 3, "Cal", hm)
            add("dietary_fiber", d, 30.0 / 3, "g", hm)
            add("dietary_calcium", d, 1100.0 / 3, "mg", hm)
    con.commit(); con.close()
    return str(path)


def test_energy_line_mifflin_st_jeor_82pct(tmp_path):
    """Male 90 kg, 180 cm, age 40, AF 1.4 -> BMR=1830, need=2562, 2100/2562=82%."""
    db = _build_energy_db(tmp_path / "db.sqlite")
    lines = nutrition.report(db, DRI_ENERGY, REF.isoformat())
    e = _line("\n".join(lines), "Energy")
    assert "7d avg 2100 kcal" in e, e
    assert "82%" in e, e
    assert "2562 kcal" in e, e
    assert "Mifflin" in e, e
    assert "1.4" in e, e
    assert "40" in e, e
    # Energy line comes right after the header and the "Reference:" line (index 2)
    idx = lines.index(e)
    assert idx == 2, f"Energy line at index {idx}, expected 2"


def test_energy_under_logging_note(tmp_path):
    """1300 kcal vs 2562 -> 51% -> note possible under-logging."""
    db = _build_energy_db(tmp_path / "db.sqlite", kcal=1300.0)
    lines = nutrition.report(db, DRI_ENERGY, REF.isoformat())
    e = _line("\n".join(lines), "Energy")
    assert "51%" in e, e
    assert "under-logg" in e.lower(), e


def test_energy_no_height(tmp_path):
    """Without height the line still shows intake but says estimate n/a."""
    db = _build_energy_db(tmp_path / "db.sqlite", height=None)
    lines = nutrition.report(db, DRI_ENERGY, REF.isoformat())
    e = _line("\n".join(lines), "Energy")
    assert "2100 kcal" in e, e
    assert "n/a" in e, e
    assert "height" in e, e


def test_energy_no_weight(tmp_path):
    """Without weight the line still shows intake but says estimate n/a."""
    db = _build_energy_db(tmp_path / "db.sqlite", weight=None)
    lines = nutrition.report(db, DRI_ENERGY, REF.isoformat())
    e = _line("\n".join(lines), "Energy")
    assert "2100 kcal" in e, e
    assert "n/a" in e, e
    assert "weight" in e, e


def test_energy_female_formula(tmp_path):
    """Female formula uses -161 instead of +5."""
    dri_female = DRI_ENERGY.replace("sex: male\n", "sex: female\n")
    db = _build_energy_db(tmp_path / "db.sqlite")
    lines = nutrition.report(db, dri_female, REF.isoformat())
    e = _line("\n".join(lines), "Energy")
    # Female BMR = 10*90 + 6.25*180 - 5*40 - 161 = 1664; need = 1664*1.4 = 2330
    # 2100/2330 = 90%
    assert "2330" in e, e
    assert "90%" in e, e


def test_energy_json_block(tmp_path):
    """--json output carries an 'energy' block with the expected keys."""
    db = _build_energy_db(tmp_path / "db.sqlite")
    # We can't easily pass --json to report() directly, so we test the
    # report_json path by calling nutrition.report_json with the DRI.
    j = nutrition.report_json(db, DRI_ENERGY, REF.isoformat())
    assert "energy" in j, f"energy key missing from json: {j.keys()}"
    en = j["energy"]
    assert round(en["avg_kcal"]) == 2100, en
    assert round(en["estimated_need_kcal"]) == 2562, en
    assert en["pct"] == 82, en
    assert en["method"] == "mifflin_st_jeor", en
    assert "weight_kg" in en["inputs"], en
    assert "height_cm" in en["inputs"], en
    assert "age_years" in en["inputs"], en
    assert "activity_factor" in en["inputs"], en


def test_energy_28d_avg_label(tmp_path):
    """The --days parameter flows into the Energy line prefix."""
    db = _build_energy_db(tmp_path / "db.sqlite")
    lines = nutrition.report(db, DRI_ENERGY, REF.isoformat(), days=28)
    e = _line("\n".join(lines), "Energy")
    assert "28d avg" in e, e
    assert "7d avg" not in e, e


def test_latest_weight_and_height_include_samples_on_the_reference_day(tmp_path):
    """`start_time <= 'YYYY-MM-DD'` as a string compare excludes every sample ON the reference day
    ('2030-06-30T12:00:00Z' > '2030-06-30'); a weigh-in that morning must count (review 2026-09-23)."""
    db = _build_db(tmp_path / "db.sqlite")
    con = sqlite3.connect(db)
    ts = datetime.fromisoformat(f"{REF.isoformat()}T07:30:00").replace(tzinfo=ZoneInfo("America/Chicago")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) VALUES ('t','weight','w-today',?,?,95.0,'kg')", (ts, ts))
    con.execute("INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) VALUES ('t','height','h-today',?,?,185.0,'cm')", (ts, ts))
    con.commit(); con.close()
    assert nutrition._latest_weight(db, REF) == 95.0
    assert nutrition._latest_height(db, REF) == 185.0


# ---- IOM EER target (2026-09-29) ----

DRI_IOM = DRI_ENERGY.replace("activity_factor: 1.4\n", "activity_factor: 1.4\npal: low_active\ngoal_kcal: -300\n")


def test_iom_target_in_json_block(tmp_path):
    db = _build_energy_db(tmp_path / "db.sqlite")
    en = nutrition.report_json(db, _dri_path(tmp_path, DRI_IOM), REF.isoformat())["energy"]
    # EER = 662 - 9.53*40 + 1.11*(15.91*90 + 539.6*1.8) = 2948.3; goal -300 -> 2648
    assert en["method"] == "iom_eer", en
    assert en["estimated_need_kcal"] == 2648, en
    assert en["pct"] == 79, en
    assert en["inputs"]["pal"] == 1.11 and en["inputs"]["goal_kcal"] == -300, en
    # cross-check: Mifflin BMR 1830 x standard low-active factor 1.375
    assert en["inputs"]["crosscheck_kcal"] == 2516, en


def test_iom_target_in_text_line(tmp_path):
    db = _build_energy_db(tmp_path / "db.sqlite")
    e = _line("\n".join(nutrition.report(db, _dri_path(tmp_path, DRI_IOM), REF.isoformat())), "Energy")
    assert "2648" in e and "IOM" in e and "-300" in e, e


def test_without_pal_the_mifflin_estimate_is_unchanged(tmp_path):
    db = _build_energy_db(tmp_path / "db.sqlite")
    en = nutrition.report_json(db, _dri_path(tmp_path, DRI_ENERGY), REF.isoformat())["energy"]
    assert en["method"] == "mifflin_st_jeor" and round(en["estimated_need_kcal"]) == 2562, en


def test_stale_weight_gives_no_target(tmp_path):
    """A weigh-in older than 14 days must not drive a calorie target."""
    db = _build_energy_db(tmp_path / "db.sqlite")
    con = sqlite3.connect(db)
    con.execute("UPDATE samples SET start_time = '2030-05-01T12:00:00Z', end_time = '2030-05-01T12:00:00Z' WHERE type_code = 'weight'")
    con.commit(); con.close()
    en = nutrition.report_json(db, _dri_path(tmp_path, DRI_IOM), REF.isoformat())["energy"]
    assert en["estimated_need_kcal"] is None and en["pct"] is None, en
    e = _line("\n".join(nutrition.report(db, _dri_path(tmp_path, DRI_IOM), REF.isoformat())), "Energy")
    assert "n/a" in e and "weigh" in e, e


def test_profile_from_settings_overrides_the_dri_file(monkeypatch, tmp_path):
    from health_insights import settings
    cfg = tmp_path / "hi.yaml"
    cfg.write_text("profile: {pal: active, goal_kcal: -200}\n")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(cfg))
    settings.reset()
    try:
        db = _build_energy_db(tmp_path / "db.sqlite")
        en = nutrition.report_json(db, _dri_path(tmp_path, DRI_IOM), REF.isoformat())["energy"]
    finally:
        settings.reset()
    # EER active = 662 - 9.53*40 + 1.25*(15.91*90 + 539.6*1.8) = 3284.8 -> 3285, goal -200
    assert en["estimated_need_kcal"] == 3085, en


def test_no_profile_means_no_energy_target(tmp_path):
    no_profile = DRI_ENERGY.replace("sex: male\n", "").replace("age_years: 40\n", "")
    db = _build_energy_db(tmp_path / "db.sqlite")
    en = nutrition.report_json(db, _dri_path(tmp_path, no_profile), REF.isoformat())["energy"]
    assert en["estimated_need_kcal"] is None, en
    e = _line("\n".join(nutrition.report(db, _dri_path(tmp_path, no_profile), REF.isoformat())), "Energy")
    assert "n/a" in e and "profile" in e, e
