"""ND-01: the shipped DRI table is structured and its loader validates the structure."""
from pathlib import Path

import pytest
import yaml

from health_insights import nutrition

DRI = Path(nutrition.__file__).parent / "data" / "dri.yaml"
FIELDS = ("population", "form", "ul_scope", "window", "review_date")
SUPPLEMENT_ONLY = {"magnesium", "niacin", "folate", "vitamin_e"}


def _raw():
    return yaml.safe_load(DRI.read_text(encoding="utf-8"))["nutrients"]


def test_every_shipped_row_has_all_structured_fields():
    for key, row in _raw().items():
        for f in FIELDS:
            assert f in row, f"{key} missing {f}"
        assert row["population"] == "male 31-50"
        assert row["review_date"] == "2026-09-23"


def test_values_are_from_allowed_sets():
    for key, row in _raw().items():
        assert row["ul_scope"] in ("food+supplements", "supplements"), key
        assert row["window"] == "daily", key


def test_supplement_only_rows():
    rows = _raw()
    for key in SUPPLEMENT_ONLY:
        assert rows[key]["ul_scope"] == "supplements", key
    assert rows["calcium"]["ul_scope"] == "food+supplements"
    assert rows["folate"]["form"] == "folic acid"
    assert rows["vitamin_a"]["form"] == "preformed retinol"


def test_loader_returns_structured_rows():
    cfg = nutrition._load_dri(str(DRI))
    assert cfg["nutrients"]["magnesium"]["ul_scope"] == "supplements"


OLD = """group: male 31-50
nutrients:
  zinc: {label: Zinc, unit: mg, type: rda, target: 11, ul: 40, apple: dietary_zinc, source: "x"}
"""


def test_old_style_yaml_gets_defaults():
    row = nutrition._load_dri(OLD)["nutrients"]["zinc"]
    assert row["population"] == "male 31-50"
    assert row["form"] == "any"
    assert row["ul_scope"] == "food+supplements"
    assert row["window"] == "daily"
    assert row["review_date"] is None
    assert row["target"] == 11


@pytest.mark.parametrize("field,bad", [("ul_scope", "pills"), ("window", "weekly")])
def test_unknown_value_raises_naming_nutrient(field, bad):
    text = OLD.replace('source: "x"', f'source: "x", {field}: {bad}')
    with pytest.raises(ValueError, match="zinc"):
        nutrition._load_dri(text)
