"""Compounds and upper-limit sections of the nutrition report (synthetic SQLite only)."""
from __future__ import annotations

import re
import sqlite3
from datetime import date

import pytest

from health_insights import nutrition

DRI = """
group: test group
energy_unit: kcal
nutrients:
  iron:      {label: Iron,      unit: mg,  type: rda, target: 8,   ul: 45,  apple: dietary_iron,      source: t}
  zinc:      {label: Zinc,      unit: mg,  type: rda, target: 11,  ul: 40,  apple: dietary_zinc,      source: t}
  folate:    {label: Folate,    unit: mcg, type: rda, target: 400, ul: 1000, apple: dietary_folate, form: folic acid, ul_scope: supplements, source: t}
  magnesium: {label: Magnesium, unit: mg,  type: rda, target: 420, ul: 350, apple: dietary_magnesium, form: supplemental magnesium, ul_scope: supplements, source: t}
  fiber:     {label: Fiber,     unit: g,   type: ai,  target: 38,  ul: null, apple: dietary_fiber,     source: t}
rules: {low_share: 0.70, low_days: 4, min_logged_days: 1, partial_day_kcal: 1200, supplement_kinds: [vitamin, mineral]}
kinds: {vitamin: [], mineral: []}
"""
START, END = date(2030, 6, 1), date(2030, 6, 2)
UUID_ZN = "00000000-0000-4000-8000-0000000000aa"

SCHEMA = """
CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, client_record_id TEXT,
    start_time TEXT, end_time TEXT, value REAL, unit TEXT, metadata_json TEXT);
CREATE TABLE sleep_sessions (sleep_session_id INTEGER PRIMARY KEY, source_id INTEGER, client_record_id TEXT, start_time TEXT, end_time TEXT);
"""
INTAKE_SCHEMA = """
CREATE TABLE intake_state (owner_id, producer_id, intake_id, deleted INTEGER DEFAULT 0);
CREATE TABLE intake_tombstones (owner_id, producer_id, intake_id);
CREATE TABLE intake_revisions (intake_revision_row_id INTEGER PRIMARY KEY, owner_id, producer_id, intake_id, revision INTEGER,
    occurred_at, time_zone, category, display_name);
CREATE TABLE intake_compound_facts (intake_revision_row_id INTEGER, component_id, position INTEGER, kind, code, label_name,
    value_state, amount, unit, quantity_basis, aggregation_role, provenance);
CREATE TABLE intake_projection_snapshots (intake_revision_row_id INTEGER, projection_sequence INTEGER);
CREATE TABLE intake_sample_links (intake_revision_row_id INTEGER, projection_sequence INTEGER, component_id, sample_uuid,
    disposition);
"""


class Db:
    def __init__(self, path, intake=True):
        self.path = str(path)
        self.con = sqlite3.connect(self.path)
        self.con.executescript(SCHEMA + (INTAKE_SCHEMA if intake else ""))
        self.n = 0

    def sample(self, code, day, value, unit, uuid=None):
        self.n += 1
        crid = f"hk-quantity-{code.replace('_', '-')}-{(uuid or f'00000000-0000-4000-8000-{self.n:012d}')}"
        self.con.execute("INSERT INTO samples (type_code, client_record_id, start_time, end_time, value, unit) VALUES (?,?,?,?,?,?)",
                         (code, crid, f"2030-06-{day}T17:00:00Z", f"2030-06-{day}T17:00:00Z", value, unit))

    def intake(self, iid, facts, category="supplement", day="01", links=()):
        rid = self.con.execute("INSERT INTO intake_revisions (owner_id, producer_id, intake_id, revision, occurred_at, time_zone, category, display_name) "
                               "VALUES ('o','p',?,1,?,'America/Chicago',?,'x')", (iid, f"2030-06-{day}T07:00:00-05:00", category)).lastrowid
        for pos, (cid, kind, code, amount, unit, label) in enumerate(facts):
            self.con.execute("INSERT INTO intake_compound_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (rid, cid, pos, kind, code, label, "known" if amount is not None else "unknown", amount, unit,
                              "compound_mass", "context_only", "user_confirmed"))
        self.con.execute("INSERT INTO intake_projection_snapshots VALUES (?,1)", (rid,))
        for cid, uuid in links:
            self.con.execute("INSERT INTO intake_sample_links VALUES (?,1,?,?,'active')", (rid, cid, uuid))

    def done(self):
        self.con.commit()
        self.con.close()
        return self.path


def _json(path):
    return nutrition.report_json(path, DRI, END.isoformat(), days=2)


def _base(tmp_path, intake=True):
    db = Db(tmp_path / "r.sqlite", intake)
    for d in ("01", "02"):
        db.sample("dietary_energy_consumed", d, 2000.0, "kcal")
    return db


def test_compounds_section_lists_totals(tmp_path):
    db = _base(tmp_path)
    db.intake("a", [("c1", "compound", "creatine", "5", "g", None)])
    db.intake("b", [("c1", "compound", "creatine", "3", "g", None)], day="02")
    out = _json(db.done())
    row = out["compounds"][0]
    assert (row["substance"], row["total"], row["unit"], row["known_count"], row["days"]) == ("creatine", "8", "g", 2, 2)
    assert out["upper_limits"] == {}


def test_ul_above_within_and_mean(tmp_path):
    db = _base(tmp_path)
    db.sample("dietary_iron", "01", 30.0, "mg")
    db.sample("dietary_iron", "02", 10.0, "mg")
    db.intake("a", [("c1", "nutrient", "dietary_iron", "20", "mg", "iron salt")])
    iron = _json(db.done())["upper_limits"]["iron"]
    assert iron["status"] == "above" and iron["high_days"] == ["2030-06-01"]
    assert iron["mean_daily"] == "30" and iron["ul"] == "45" and iron["days_counted"] == 2


def test_ul_within_with_sample_only(tmp_path):
    db = _base(tmp_path)
    db.sample("dietary_iron", "01", 10.0, "mg")
    iron = _json(db.done())["upper_limits"]["iron"]
    assert iron["status"] == "within" and iron["high_days"] == [] and iron["mean_daily"] == "10"


def test_ul_within_with_intake_only(tmp_path):
    db = _base(tmp_path)
    db.intake("a", [("c1", "nutrient", "dietary_iron", "10", "mg", None)], category="food")
    iron = _json(db.done())["upper_limits"]["iron"]
    assert iron["status"] == "within" and iron["mean_daily"] == "10"


def test_ul_undetermined_for_unknown_amount_or_unit(tmp_path):
    db = _base(tmp_path)
    db.intake("a", [("c1", "nutrient", "dietary_iron", None, "mg", None)])
    db.intake("b", [("c1", "nutrient", "dietary_zinc", "5", "g", None)], day="02")
    out = _json(db.done())["upper_limits"]
    assert out["iron"]["status"] == "undetermined" and out["iron"]["undetermined_days"] == ["2030-06-01"]
    assert out["zinc"]["status"] == "undetermined" and out["zinc"]["undetermined_days"] == ["2030-06-02"]


def test_no_ul_data_omits_nutrient(tmp_path):
    db = _base(tmp_path)
    db.sample("dietary_fiber", "01", 30.0, "g")
    out = _json(db.done())
    assert "fiber" not in out["upper_limits"] and "iron" not in out["upper_limits"]


def test_linked_sample_counted_once_and_attributed(tmp_path):
    db = _base(tmp_path)
    db.sample("dietary_zinc", "01", 10.0, "mg", uuid=UUID_ZN)
    db.intake("a", [("z", "nutrient", "dietary_zinc", "10", "mg", "zinc form")], links=[("z", UUID_ZN)])
    zinc = _json(db.done())["upper_limits"]["zinc"]
    assert zinc["mean_daily"] == "10" and zinc["status"] == "within" and zinc["days_counted"] == 1


def test_supplement_category_and_form_scope(tmp_path):
    db = _base(tmp_path)
    db.intake("a", [("f", "nutrient", "dietary_folate", "1200", "mcg", "folic acid"),
                    ("g", "nutrient", "dietary_folate", "300", "mcg", "folate from food")], day="01")
    db.intake("b", [("f", "nutrient", "dietary_folate", "900", "mcg", "folic acid")], category="food", day="02")
    fol = _json(db.done())["upper_limits"]["folate"]
    assert fol["status"] == "above" and fol["high_days"] == ["2030-06-01"]
    assert fol["ul_form"] == "folic acid" and fol["ul_scope"] == "supplements"
    assert fol["days_counted"] == 1  # the food-category day does not count toward a supplements-only UL


def test_supplements_only_ul_ignores_food_and_unknown_source_is_undetermined(tmp_path):
    db = _base(tmp_path)
    db.sample("dietary_magnesium", "02", 500.0, "mg")
    db.intake("a", [("m", "nutrient", "dietary_magnesium", "100", "mg", None)], category="food", day="01")
    db.intake("b", [("m", "nutrient", "dietary_magnesium", "100", "mg", None)], day="01")
    mg = _json(db.done())["upper_limits"]["magnesium"]
    assert mg["status"] == "undetermined" and mg["undetermined_days"] == ["2030-06-02"]
    assert mg["mean_daily"] == "100" and mg["ul_form"] == "any"


def test_db_without_intake_tables(tmp_path):
    db = _base(tmp_path, intake=False)
    db.sample("dietary_iron", "01", 50.0, "mg")
    path = db.done()
    out = _json(path)
    assert out["compounds"] == [] and out["upper_limits"] == {}
    assert "nutrients" in out
    text = nutrition.report(path, DRI, END.isoformat(), days=2)
    assert not any("Upper limits" in l or "Compounds" in l for l in text)


def test_text_sections_and_no_advice_words(tmp_path):
    db = _base(tmp_path)
    db.sample("dietary_iron", "01", 30.0, "mg")
    db.intake("a", [("c1", "compound", "creatine", "5", "g", "monohydrate"),
                    ("c2", "nutrient", "dietary_iron", "20", "mg", None)])
    path = db.done()
    text = nutrition.report(path, DRI, END.isoformat(), days=2)
    joined = "\n".join(text)
    assert "creatine (monohydrate): 5 g total" in joined
    assert re.search(r"Iron: above \(UL 45 mg", joined)
    assert text[-1] == "Informational, not medical advice."
    new = [l for l in text if l.startswith("  ") or l.startswith(("Compounds", "Upper limits"))]
    assert new and not re.search(r"\b(should|recommend|reduce|increase|take less|take more|consult|stop taking|safe)\b", "\n".join(new), re.I)


def _walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


HOSTILE = ["alpha\nbeta", "alpha\rbeta", "alpha\n# Heading", "alpha # Heading x\ty",
           "x" * 300, "  spaced \t\t out  "]


@pytest.mark.parametrize("label", HOSTILE)
def test_intake_text_is_sanitized(tmp_path, label):
    db = _base(tmp_path)
    db.intake("a", [("c1", "compound", "creatine", "5", "g", label)])
    out = _json(db.done())
    row = out["compounds"][0]
    assert type(row["form"]) is str
    assert 0 < len(row["form"]) <= 80
    for s in _walk_strings(out["compounds"]):
        assert not re.search(r"[\x00-\x1f\x7f  \x85]", s)
        assert len(s) <= 80
    lines = nutrition_intake_lines(out)
    for line in lines:
        assert "\n" not in line and "\r" not in line
        assert not line.lstrip().startswith("#")
    assert any(l.startswith("  creatine") for l in lines)


def nutrition_intake_lines(out):
    from health_insights import nutrition_intake
    return nutrition_intake.text_lines(out["compounds"], out["upper_limits"], nutrition.load_dri_text(DRI)
                                       if hasattr(nutrition, "load_dri_text") else {"nutrients": {}},
                                       START, END)


def test_clean_text_rules():
    from health_insights.nutrition_intake import clean_text
    assert clean_text(None) is None
    assert clean_text("a\r\n\t b") == "a b"
    s = clean_text("y" * 200)
    assert len(s) == 80 and s.endswith("…")
    assert clean_text("ok") == "ok"
