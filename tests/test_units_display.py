"""Weight display unit is a setting (kg default, lb by config or env); storage stays kg."""
import pytest

from health_insights import settings, units


@pytest.fixture
def unit(monkeypatch):
    def _set(value):
        if value is None:
            monkeypatch.delenv("HEALTH_INSIGHTS_WEIGHT_UNIT", raising=False)
            monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", "/nonexistent/x.yaml")
        else:
            monkeypatch.setenv("HEALTH_INSIGHTS_WEIGHT_UNIT", value)
        settings.reset()
    yield _set
    settings.reset()


def test_default_is_kg(unit):
    unit(None)
    assert units.weight_unit() == "kg"
    assert units.to_display_weight(80.0) == 80.0
    assert units.to_display_weight(None) is None


def test_lb_converts(unit):
    unit("lb")
    assert units.weight_unit() == "lb"
    assert units.to_display_weight(100.0) == pytest.approx(220.462)


def test_bad_value_falls_back_to_kg(unit):
    unit("stone")
    assert units.weight_unit() == "kg"


def test_config_file_sets_unit(monkeypatch, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("weight_unit: lb\n")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(cfg))
    monkeypatch.delenv("HEALTH_INSIGHTS_WEIGHT_UNIT", raising=False)
    settings.reset()
    assert units.weight_unit() == "lb"
    settings.reset()
