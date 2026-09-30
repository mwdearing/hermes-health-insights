"""Receiver-database resolution: --db, HEALTH_INSIGHTS_BRIDGE_DB, bridge_db in config, ~/.config/healthrelay/db-path."""
import pytest

from health_insights import cli, demo, settings


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    for var in ("HEALTH_INSIGHTS_BRIDGE_DB", "HEALTH_INSIGHTS_CONFIG", "HEALTH_INSIGHTS_DATA_DIR",
                "HEALTH_INSIGHTS_MODULES_FILE", "HEALTH_INSIGHTS_MODULES"):
        monkeypatch.delenv(var, raising=False)
    settings.reset()
    yield home
    settings.reset()


@pytest.fixture()
def demo_db(env, tmp_path):
    path = tmp_path / "demo.sqlite"
    demo.build(str(path))
    return path


def _config_file(home):
    return home / ".config" / "health-insights" / "config.yaml"


def _write_config(home, text):
    path = _config_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    settings.reset()


def _write_db_path(home, text):
    path = home / ".config" / "healthrelay" / "db-path"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


TAIL = "Set it with --db, HEALTH_INSIGHTS_BRIDGE_DB, bridge_db in {cfg}, or ~/.config/healthrelay/db-path.\n"


# ---- 1.3: one clear message, exit 2, no traceback ---------------------------------------

@pytest.mark.parametrize("argv_tail", [
    ["weekly", "--db", "{p}"],
    ["concerns", "--db", "{p}"],
    ["nutrition", "--db", "{p}"],
    ["daily", "{p}", "resting_heart_rate"],
    ["coverage", "{p}"],
    ["baseline", "{p}", "resting_heart_rate"],
    ["anomalies", "{p}"],
    ["bp", "{p}"],
    ["modules", "info", "glp1", "--db", "{p}"],
    ["workouts", "digest-line", "--db", "{p}", "--date", "2026-01-01"],
    ["workouts", "weekly-line", "--db", "{p}", "--date", "2026-01-01"],
    ["labs", "import-bridge", "--bridge", "{p}", "--db", "{l}"],
    ["ecg", "import-bridge", "--bridge", "{p}", "--db", "{l}"],
])
def test_missing_database_is_one_clear_line(argv_tail, env, tmp_path, capsys):
    p = str(tmp_path / "nope.sqlite")
    argv = [a.format(p=p, l=str(tmp_path / "other.sqlite")) for a in argv_tail]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"health-insights: cannot open the receiver database at {p}: ")
    assert lines[0].endswith(TAIL.format(cfg=settings._config_path()).strip())
    assert "Traceback" not in captured.err


def test_modules_report_missing_database(env, tmp_path, capsys):
    settings.reset()
    (env / ".config" / "health-insights").mkdir(parents=True)
    (env / ".config" / "health-insights" / "modules.yaml").write_text("glp1: true\n")
    settings.reset()
    p = str(tmp_path / "nope.sqlite")
    assert cli.main(["modules", "report", "glp1", "--db", p]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"health-insights: cannot open the receiver database at {p}: ")


def test_file_that_is_not_sqlite(env, tmp_path, capsys):
    bad = tmp_path / "junk.sqlite"
    bad.write_text("this is not a database")
    assert cli.main(["weekly", "--db", str(bad)]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"health-insights: cannot open the receiver database at {bad}: ")
    assert "Traceback" not in err


def test_concerns_json_missing_db_also_exits_2(env, tmp_path, capsys):
    assert cli.main(["concerns", "--json", "--db", str(tmp_path / "nope.sqlite")]) == 2
    assert capsys.readouterr().out == ""


def test_concerns_evaluate_still_returns_unavailable_finding(env, tmp_path):
    from health_insights import concerns
    findings = concerns.evaluate(str(tmp_path / "nope.sqlite"), "2026-01-01")
    assert [f.id for f in findings] == ["concerns_unavailable"]


# ---- 1.10 (a): level word in the text form ---------------------------------------------

def test_concerns_text_prints_format_line(env, monkeypatch, capsys, demo_db):
    from health_insights import concerns
    fake = [concerns.Finding(id="x", level=1, title="Some title", evidence="some evidence", source="t", advice="a"),
            concerns.Finding(id="y", level=3, title="Bad", evidence="worse", source="t", advice="a")]
    monkeypatch.setattr(concerns, "evaluate", lambda db, date: fake)
    assert cli.main(["concerns", "--db", str(demo_db)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [concerns.format_line(f) for f in fake]
    assert out[0] == "👀 [worth watching] Some title — some evidence"


# ---- 1.7: fallback chain -----------------------------------------------------------------

def test_settings_chain_order(env, tmp_path):
    assert settings.bridge_db() is None
    _write_db_path(env, "/from/db-path.sqlite\nsecond line ignored\n")
    assert settings.bridge_db() == "/from/db-path.sqlite"
    _write_config(env, "bridge_db: /from/config.sqlite\n")
    assert settings.bridge_db() == "/from/config.sqlite"
    import os
    os.environ["HEALTH_INSIGHTS_BRIDGE_DB"] = "/from/env.sqlite"
    try:
        assert settings.bridge_db() == "/from/env.sqlite"
    finally:
        del os.environ["HEALTH_INSIGHTS_BRIDGE_DB"]


def test_db_path_file_edge_cases(env):
    _write_db_path(env, "\n")
    assert settings.bridge_db() is None
    _write_db_path(env, "  /padded/path.sqlite  \n")
    assert settings.bridge_db() == "/padded/path.sqlite"


def test_explicit_db_beats_everything(env, demo_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HEALTH_INSIGHTS_BRIDGE_DB", str(tmp_path / "env-nope.sqlite"))
    _write_config(env, f"bridge_db: {tmp_path / 'cfg-nope.sqlite'}\n")
    _write_db_path(env, str(tmp_path / "file-nope.sqlite"))
    assert cli.main(["weekly", "--db", str(demo_db)]) == 0
    assert capsys.readouterr().out.strip()


def test_env_beats_config_and_db_path(env, demo_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HEALTH_INSIGHTS_BRIDGE_DB", str(demo_db))
    _write_config(env, f"bridge_db: {tmp_path / 'cfg-nope.sqlite'}\n")
    _write_db_path(env, str(tmp_path / "file-nope.sqlite"))
    assert cli.main(["weekly"]) == 0
    assert capsys.readouterr().out.strip()


def test_config_beats_db_path(env, demo_db, tmp_path, capsys):
    _write_config(env, f"bridge_db: {demo_db}\n")
    _write_db_path(env, str(tmp_path / "file-nope.sqlite"))
    assert cli.main(["weekly"]) == 0
    assert capsys.readouterr().out.strip()


def test_db_path_file_is_the_last_step(env, demo_db, capsys):
    _write_db_path(env, f"{demo_db}\n")
    assert cli.main(["weekly"]) == 0
    assert capsys.readouterr().out.strip()


@pytest.mark.parametrize("argv", [
    ["weekly"], ["concerns"], ["nutrition"], ["coverage"], ["anomalies"], ["bp"],
    ["daily", "resting_heart_rate"], ["baseline", "resting_heart_rate"],
    ["workouts", "digest-line", "--date", "2026-01-01"],
])
def test_nothing_configured_says_so(argv, env, capsys):
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert err == ("health-insights: cannot open the receiver database at (not set): no database configured. "
                   + TAIL.format(cfg=settings._config_path()))


def test_modules_report_nothing_configured(env, capsys):
    (env / ".config" / "health-insights").mkdir(parents=True)
    (env / ".config" / "health-insights" / "modules.yaml").write_text("glp1: true\n")
    settings.reset()
    assert cli.main(["modules", "report", "glp1"]) == 2
    assert "no database configured" in capsys.readouterr().err


def test_import_bridge_uses_chain(env, demo_db, tmp_path, capsys):
    _write_db_path(env, f"{demo_db}\n")
    labs = tmp_path / "labs.sqlite"
    rc = cli.main(["labs", "import-bridge", "--db", str(labs)])
    assert rc == 0


def test_daily_accepts_both_forms(env, demo_db, monkeypatch, capsys):
    assert cli.main(["daily", str(demo_db), "resting_heart_rate"]) == 0
    explicit = capsys.readouterr().out
    assert explicit.strip()
    monkeypatch.setenv("HEALTH_INSIGHTS_BRIDGE_DB", str(demo_db))
    assert cli.main(["daily", "resting_heart_rate"]) == 0
    assert capsys.readouterr().out == explicit
    assert cli.main(["baseline", "resting_heart_rate"]) == 0
    assert capsys.readouterr().out.strip()


def test_daily_without_type_is_a_usage_error(env, demo_db, monkeypatch):
    monkeypatch.setenv("HEALTH_INSIGHTS_BRIDGE_DB", str(demo_db))
    with pytest.raises(SystemExit) as exc:
        cli.main(["daily"])
    assert exc.value.code == 2


def test_positional_and_flag_forms_agree(env, demo_db, capsys):
    assert cli.main(["bp", str(demo_db), "--date", "2026-01-01"]) == 0
    a = capsys.readouterr().out
    assert a.strip()
    assert cli.main(["bp", "--db", str(demo_db), "--date", "2026-01-01"]) == 0
    assert capsys.readouterr().out == a


def test_labs_db_defaults_to_labs_dir(env, capsys):
    assert cli.main(["labs", "summary"]) == 0
    assert f"No labs data at {settings.labs_dir() / 'labs.sqlite'}" in capsys.readouterr().out
