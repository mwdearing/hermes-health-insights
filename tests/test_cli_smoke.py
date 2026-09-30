"""CLI smoke tests: every subcommand runs through main([...]) on a synthetic demo database."""
import pytest

from health_insights import cli, demo


@pytest.fixture()
def demo_db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    path = tmp_path / "demo.sqlite"
    demo.build(str(path))
    return path


def _cases(db, tmp_path):
    labs = str(tmp_path / "labs.sqlite")
    return {
        "coverage": ["coverage", str(db)],
        "daily": ["daily", str(db), "resting_heart_rate"],
        "baseline": ["baseline", str(db), "resting_heart_rate"],
        "anomalies": ["anomalies", str(db)],
        "bp": ["bp", str(db)],
        "weekly": ["weekly", "--db", str(db)],
        "concerns": ["concerns", "--db", str(db)],
        "concerns_json": ["concerns", "--db", str(db), "--json"],
        "nutrition": ["nutrition", "--db", str(db)],
        "workouts": ["workouts", "digest-line", "--db", str(db), "--date", "2026-01-01"],
        "modules_list": ["modules", "list"],
        "labs_summary": ["labs", "summary", "--db", labs],
    }


@pytest.mark.parametrize(
    "name",
    ["coverage", "daily", "baseline", "anomalies", "bp", "weekly", "concerns", "concerns_json",
     "nutrition", "workouts", "modules_list", "labs_summary"],
)
def test_subcommand_runs(name, demo_db, tmp_path, capsys):
    argv = _cases(demo_db, tmp_path)[name]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    if name != "workouts":  # the demo data has no workouts, so its digest line is legitimately empty
        assert out.strip()


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    from health_insights import __version__
    assert capsys.readouterr().out.strip() == f"health-insights {__version__}"


@pytest.mark.parametrize("argv", [["--help"], ["weekly", "--help"], ["anomalies", "--help"], ["bp", "--help"],
                                  ["nutrition", "--help"], ["concerns", "--help"]])
def test_help_uses_program_name_and_configured_zone(argv, capsys):
    with pytest.raises(SystemExit):
        cli.main(argv)
    out = capsys.readouterr().out
    assert "Chicago" not in out
    assert "usage: health-insights" in out
