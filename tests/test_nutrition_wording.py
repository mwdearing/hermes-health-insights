"""Wording tests: the nutrition output names its reference table and recommends nothing; concerns state their window."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml

from health_insights import cli, modules, nutrition, settings
from health_insights.concern_rules import nutrition_rules as rules

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_nutrition import DRI_SMALL, REF, _build_db  # noqa: E402
from test_nutrition_rules import DRI, _make_db  # noqa: E402

SHIPPED = str(settings.package_data("dri.yaml"))
DEFAULT_LINE = "Reference: Dietary Reference Intakes for adult men 31 to 50 (default table; use --dri for others)"
CUSTOM_LINE = "Reference: Dietary Reference Intakes for adult men 31 to 50 (custom table from --dri)"


def test_text_report_names_the_shipped_table(tmp_path):
    db = _build_db(tmp_path / "db.sqlite")
    lines = nutrition.report(db, SHIPPED, REF.isoformat(), days=7)
    assert lines[0].startswith("Nutrition ")
    assert lines[1] == DEFAULT_LINE


def test_text_report_names_a_custom_table(tmp_path):
    db = _build_db(tmp_path / "db.sqlite")
    lines = nutrition.report(db, DRI_SMALL, REF.isoformat(), days=7)
    assert lines[1] == CUSTOM_LINE


def test_json_carries_dri_table(tmp_path):
    db = _build_db(tmp_path / "db.sqlite")
    j = nutrition.report_json(db, SHIPPED, REF.isoformat(), days=7)
    assert j["dri_table"] == {"group": "male 31-50", "label": "adult men 31 to 50", "default": True}
    j2 = nutrition.report_json(db, DRI_SMALL, REF.isoformat(), days=7)
    assert j2["dri_table"]["default"] is False


def test_cli_nutrition_prints_the_reference_line(tmp_path, capsys):
    db = _build_db(tmp_path / "db.sqlite")
    assert cli.main(["nutrition", "--db", db, "--date", REF.isoformat()]) == 0
    assert DEFAULT_LINE in capsys.readouterr().out


def test_report_recommends_no_supplements(tmp_path):
    db = _build_db(tmp_path / "db.sqlite", iron=1.0)
    text = "\n".join(nutrition.report(db, DRI_SMALL, REF.isoformat(), days=7))
    assert "upplement" not in text
    assert any(l.startswith("Consistently below target: ") for l in text.splitlines())
    if "Consistently below target: none" not in text:
        assert "(worth discussing with a clinician or dietitian)" in text


def test_json_keys_are_unchanged(tmp_path):
    db = _build_db(tmp_path / "db.sqlite")
    assert "supplement_candidates" in nutrition.report_json(db, DRI_SMALL, REF.isoformat(), days=7)


def _finding(days_data):
    db = _make_db(days_data)
    dri = tempfile.mktemp(suffix=".yaml")
    yaml.safe_dump({**DRI, "group": "male 31-50"}, open(dri, "w"))
    try:
        found = rules.nutrition_gap_findings(db, "2031-09-20", dri_path=dri)
    finally:
        os.unlink(dri)
        os.unlink(db)
    assert len(found) == 1
    return found[0]


def test_one_low_nutrient_is_singular_and_names_window_and_table():
    f = _finding({b: {"dietary_folate": 60} for b in range(10)})
    assert "several" not in f.advice
    assert "one nutrient" in f.advice
    assert "28-day average" in f.advice
    assert "adult men 31 to 50" in f.advice


def test_two_low_nutrients_are_counted():
    f = _finding({b: {"dietary_folate": 60, "dietary_zinc": 2} for b in range(10)})
    assert "several" not in f.advice
    assert "2 nutrients" in f.advice
    assert "28-day average" in f.advice


def test_medication_adherence_is_labelled(capsys):
    assert cli.main(["modules", "list"]) == 0
    listing = capsys.readouterr().out
    line = next(l for l in listing.splitlines() if "medication_adherence" in l)
    assert "needs the medlog CLI (not included)" in line
    assert cli.main(["modules", "info", "medication_adherence"]) == 0
    assert "needs the medlog CLI (not included)" in capsys.readouterr().out
    others = [l for l in listing.splitlines() if "medlog" in l]
    assert len(others) == 1


def test_only_medication_adherence_carries_the_label():
    for module in modules.REGISTRY.values():
        has = "needs the medlog CLI" in module.summary
        assert has == (module.id == "medication_adherence")
