"""Opt-in modules: one clear registry, one config file the tool owns, easy CLI toggles, a contract every module meets."""
import json
import os
import stat

import pytest

from health_insights import cli, demo, modules, settings


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("HEALTH_INSIGHTS_MODULES_FILE", str(tmp_path / "modules.yaml"))
    monkeypatch.delenv("HEALTH_INSIGHTS_MODULES", raising=False)
    monkeypatch.setenv("HEALTH_INSIGHTS_MEDLOG", "0")
    settings.reset()
    yield
    settings.reset()


def _fake_rule(db, ref):
    return []


@pytest.fixture
def fake_module():
    m = modules.Module(
        id="fake_test_module", title="Fake", summary="A module used only by tests.", category="test",
        data_needs=("weight",), rules=(("fake", _fake_rule),), skills=(), cautions=("Not a real module.",))
    modules.register(m)
    yield m
    modules.REGISTRY.pop(m.id, None)


def test_every_module_meets_the_contract():
    assert modules.REGISTRY, "at least one module must be registered"
    for m in modules.REGISTRY.values():
        assert m.id and m.id == m.id.lower() and " " not in m.id
        assert m.title and m.summary and m.category
        assert isinstance(m.cautions, tuple) and m.cautions, f"{m.id} needs at least one caution"
        assert isinstance(m.data_needs, tuple)
        assert m.default_enabled is False, f"{m.id}: optional modules must be off by default"


def test_all_off_by_default():
    for m in modules.REGISTRY.values():
        assert modules.is_enabled(m.id) is False


def test_set_module_round_trip_and_permissions(fake_module, tmp_path):
    modules.set_enabled(fake_module.id, True)
    assert modules.is_enabled(fake_module.id) is True
    mode = stat.S_IMODE(os.stat(tmp_path / "modules.yaml").st_mode)
    assert mode == 0o600
    modules.set_enabled(fake_module.id, False)
    assert modules.is_enabled(fake_module.id) is False


def test_env_list_enables(fake_module, monkeypatch):
    monkeypatch.setenv("HEALTH_INSIGHTS_MODULES", f"other, {fake_module.id}")
    settings.reset()
    assert modules.is_enabled(fake_module.id) is True


def test_config_yaml_modules_block_also_works(fake_module, tmp_path):
    (tmp_path / "config.yaml").write_text(f"modules:\n  {fake_module.id}: true\n")
    settings.reset()
    assert modules.is_enabled(fake_module.id) is True


def test_modules_file_wins_over_config_yaml(fake_module, tmp_path):
    (tmp_path / "config.yaml").write_text(f"modules:\n  {fake_module.id}: true\n")
    (tmp_path / "modules.yaml").write_text(f"{fake_module.id}: false\n")
    settings.reset()
    assert modules.is_enabled(fake_module.id) is False


def test_unknown_module_is_an_error():
    with pytest.raises(KeyError):
        modules.set_enabled("no_such_module", True)
    with pytest.raises(KeyError):
        modules.is_enabled("no_such_module")


def test_module_rules_only_when_enabled(fake_module):
    assert modules.module_rules() == []
    modules.set_enabled(fake_module.id, True)
    assert [name for name, _ in modules.module_rules()] == ["fake"]


def test_medlog_alias_enables_the_adherence_module(monkeypatch):
    monkeypatch.setenv("HEALTH_INSIGHTS_MEDLOG", "1")
    settings.reset()
    assert modules.is_enabled("medication_adherence") is True


def test_options_come_from_a_mapping_value(fake_module, tmp_path):
    (tmp_path / "modules.yaml").write_text(f"{fake_module.id}:\n  enabled: true\n  options: {{threshold: 3}}\n")
    settings.reset()
    assert modules.is_enabled(fake_module.id) is True
    assert modules.options(fake_module.id) == {"threshold": 3}


def test_readiness_reports_present_and_missing_data(tmp_path, fake_module):
    db = str(tmp_path / "d.sqlite")
    demo.build(db, days=20)
    info = modules.readiness(fake_module.id, db)
    assert info["present"] == ["weight"] and info["missing"] == []
    m2 = modules.Module(id="fake_two", title="T", summary="S", category="test",
                        data_needs=("weight", "blood_glucose"), rules=(), skills=(), cautions=("c",))
    modules.register(m2)
    try:
        assert modules.readiness("fake_two", db)["missing"] == ["blood_glucose"]
    finally:
        modules.REGISTRY.pop("fake_two", None)


def test_cli_list_enable_disable_info(fake_module, capsys):
    assert cli.main(["modules", "list"]) == 0
    out = capsys.readouterr().out
    assert fake_module.id in out and "off" in out
    assert cli.main(["modules", "enable", fake_module.id]) == 0
    assert modules.is_enabled(fake_module.id)
    assert cli.main(["modules", "list"]) == 0
    assert "on" in capsys.readouterr().out
    assert cli.main(["modules", "info", fake_module.id]) == 0
    assert "Not a real module." in capsys.readouterr().out
    assert cli.main(["modules", "disable", fake_module.id]) == 0
    assert not modules.is_enabled(fake_module.id)


def test_cli_list_json_and_unknown_id(fake_module, capsys):
    assert cli.main(["modules", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["id"] == fake_module.id and r["enabled"] is False for r in rows)
    assert cli.main(["modules", "enable", "nope"]) == 2


def test_concerns_engine_includes_enabled_module_rules(fake_module):
    from health_insights import concerns
    calls = []
    orig = fake_module
    modules.REGISTRY[orig.id] = modules.Module(**{**orig.__dict__, "rules": (("fake", lambda db, ref: calls.append(1) or []),)})
    modules.set_enabled(orig.id, True)
    concerns.evaluate("ignored.db", "2030-06-30", rules=None)
    assert calls, "enabled module rules must run inside concerns.evaluate"


@pytest.mark.parametrize("text", [
    "You should increase your dose next week.", "Consider skipping a dose.", "Try to reduce your medication.",
    "Double the dose if this continues.", "Time to titrate upward.",
])
def test_wording_lint_rejects_dosing_advice(text):
    assert modules.lint_advice(text), text


@pytest.mark.parametrize("text", [
    "Talk to your clinician about this trend.", "No dosing advice is given here.",
    "Resting heart rate is 6 bpm above your 28-day median.", "Seek urgent care if you have severe abdominal pain.",
])
def test_wording_lint_allows_safe_text(text):
    assert modules.lint_advice(text) == [], text


def test_every_module_skill_exists_in_the_plugin():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    skills_dir = root / "public" / "skills" if (root / "public" / "skills").is_dir() else root / "skills"
    for m in modules.REGISTRY.values():
        for skill in m.skills:
            assert (skills_dir / skill / "SKILL.md").is_file(), f"{m.id}: missing skill {skill}"
