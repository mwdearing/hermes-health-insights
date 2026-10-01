"""Settings layer: timezone comes from env, then the config file, then the system, never a hard-coded city."""
import pytest

from health_insights import settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("HEALTH_INSIGHTS_TZ", raising=False)
    monkeypatch.delenv("HEALTH_INSIGHTS_CONFIG", raising=False)
    settings.reset()
    yield
    settings.reset()


def test_env_timezone_wins(monkeypatch, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("timezone: Europe/Berlin\n")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(cfg))
    monkeypatch.setenv("HEALTH_INSIGHTS_TZ", "America/Denver")
    settings.reset()
    assert settings.timezone_name() == "America/Denver"


def test_config_file_timezone(monkeypatch, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("timezone: Europe/Berlin\n")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(cfg))
    settings.reset()
    assert settings.timezone_name() == "Europe/Berlin"
    assert str(settings.timezone()) == "Europe/Berlin"


def test_default_is_a_valid_zone_without_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(tmp_path / "missing.yaml"))
    settings.reset()
    assert settings.timezone().key  # resolves to some real zone, no exception


def test_label_is_the_city_part(monkeypatch):
    monkeypatch.setenv("HEALTH_INSIGHTS_TZ", "America/Los_Angeles")
    settings.reset()
    assert settings.tz_label() == "Los Angeles"


def test_bad_zone_falls_back_to_utc(monkeypatch):
    monkeypatch.setenv("HEALTH_INSIGHTS_TZ", "Not/AZone")
    settings.reset()
    assert settings.timezone_name() == "UTC"


def test_data_dir_env_then_config_then_home_default(monkeypatch, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"data_dir: {tmp_path}/from-config\n")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(cfg))
    monkeypatch.delenv("HEALTH_INSIGHTS_DATA_DIR", raising=False)
    settings.reset()
    assert str(settings.data_dir()) == f"{tmp_path}/from-config"
    monkeypatch.setenv("HEALTH_INSIGHTS_DATA_DIR", str(tmp_path / "from-env"))
    settings.reset()
    assert settings.data_dir() == tmp_path / "from-env"
    monkeypatch.delenv("HEALTH_INSIGHTS_DATA_DIR")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(tmp_path / "none.yaml"))
    settings.reset()
    assert str(settings.data_dir()).endswith(".local/share/health-insights")


def test_derived_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_INSIGHTS_DATA_DIR", str(tmp_path))
    settings.reset()
    assert settings.labs_dir() == tmp_path / "labs"
    assert settings.ecg_dir() == tmp_path / "ecg"
    assert settings.cards_dir() == tmp_path / "cards"


def test_bridge_db_env_config_or_none(monkeypatch, tmp_path):
    monkeypatch.delenv("HEALTH_INSIGHTS_BRIDGE_DB", raising=False)
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(tmp_path / "none.yaml"))
    settings.reset()
    assert settings.bridge_db() is None
    monkeypatch.setenv("HEALTH_INSIGHTS_BRIDGE_DB", "/x/device.sqlite")
    settings.reset()
    assert settings.bridge_db() == "/x/device.sqlite"


def test_integrations_are_off_by_default(monkeypatch, tmp_path):
    for var in ("HEALTH_INSIGHTS_MEDLOG", "HEALTH_INSIGHTS_NARRATION_URL", "MEDLOG_BIN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(tmp_path / "none.yaml"))
    settings.reset()
    assert settings.medlog_enabled() is False
    assert settings.narration_url() is None
    assert settings.medlog_bin() == "medlog"


def test_integrations_from_config_and_env(monkeypatch, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("integrations: {medlog: true}\nnarration: {url: 'http://h/v1', model: m}\nmedlog_path: /p\n")
    monkeypatch.setenv("HEALTH_INSIGHTS_CONFIG", str(cfg))
    for var in ("HEALTH_INSIGHTS_MEDLOG", "HEALTH_INSIGHTS_NARRATION_URL"):
        monkeypatch.delenv(var, raising=False)
    settings.reset()
    assert settings.medlog_enabled() is True
    assert settings.narration_url() == "http://h/v1"
    assert settings.narration_model() == "m"
    assert settings.medlog_path() == "/p"
    monkeypatch.setenv("HEALTH_INSIGHTS_MEDLOG", "0")
    settings.reset()
    assert settings.medlog_enabled() is False


def test_shipped_config_files_live_inside_the_package():
    for name in ("health_monitor.yaml", "dri.yaml"):
        path = settings.package_data(name)
        assert path.is_file() and "health_insights" in str(path), path


@pytest.fixture(autouse=True)
def _no_real_db_path_file(monkeypatch, tmp_path_factory):
    """bridge_db() falls back to ~/.config/healthrelay/db-path: never read the real one."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    settings.reset()
