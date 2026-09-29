"""Deployment settings: timezone first. Resolution order is env, config file, system, then UTC.

Config file: $HEALTH_INSIGHTS_CONFIG or ~/.config/health-insights/config.yaml (YAML mapping).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


def package_data(name: str) -> Path:
    """A default file shipped inside the package (health_monitor.yaml, dri.yaml)."""
    return Path(__file__).resolve().parent / "data" / name


def _config_path() -> Path:
    override = os.environ.get("HEALTH_INSIGHTS_CONFIG")
    return Path(override) if override else Path.home() / ".config" / "health-insights" / "config.yaml"


@lru_cache(maxsize=1)
def _config() -> dict:
    path = _config_path()
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _system_zone() -> str:
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        return Path("/etc/timezone").read_text().strip() or "UTC"
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


@lru_cache(maxsize=1)
def timezone_name() -> str:
    name = os.environ.get("HEALTH_INSIGHTS_TZ") or _config().get("timezone") or _system_zone()
    try:
        ZoneInfo(name)
    except Exception:
        return "UTC"
    return name


def timezone() -> ZoneInfo:
    return ZoneInfo(timezone_name())


def tz_label() -> str:
    """Short place name for report headers, e.g. 'Chicago' for America/Chicago."""
    return timezone_name().split("/")[-1].replace("_", " ")


def data_dir() -> Path:
    """Where snapshots, merged history, labs, ECG and cards live."""
    value = os.environ.get("HEALTH_INSIGHTS_DATA_DIR") or _config().get("data_dir")
    return Path(value).expanduser() if value else Path.home() / ".local" / "share" / "health-insights"


def labs_dir() -> Path:
    return data_dir() / "labs"


def ecg_dir() -> Path:
    return data_dir() / "ecg"


def cards_dir() -> Path:
    return data_dir() / "cards"


def bridge_db() -> str | None:
    """Path of the receiver's live database, or None when not configured."""
    return os.environ.get("HEALTH_INSIGHTS_BRIDGE_DB") or _config().get("bridge_db") or None


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def medlog_enabled() -> bool:
    """Medication-adherence integration (the `medlog` CLI). Off unless enabled by env or config."""
    env = os.environ.get("HEALTH_INSIGHTS_MEDLOG")
    if env is not None:
        return _truthy(env)
    return _truthy((_config().get("integrations") or {}).get("medlog", False))


def medlog_bin() -> str:
    return os.environ.get("MEDLOG_BIN") or _config().get("medlog_bin") or "medlog"


def medlog_path() -> str | None:
    """Directory of the medication tracker's Python module, when it is not installed."""
    return _config().get("medlog_path") or None


def notify_command() -> str | None:
    """Executable called as `cmd TITLE MESSAGE` for urgent findings. None = no push."""
    return os.environ.get("HEALTH_INSIGHTS_NOTIFY") or _config().get("notify_command") or None


def narration_url() -> str | None:
    """OpenAI-compatible chat-completions URL of a LOCAL model. None = deterministic text only."""
    return os.environ.get("HEALTH_INSIGHTS_NARRATION_URL") or (_config().get("narration") or {}).get("url") or None


def narration_model() -> str:
    return (_config().get("narration") or {}).get("model") or "local"


_PROFILE_KEYS = ("sex", "age_years", "pal", "goal_kcal", "activity_factor")


def profile() -> dict:
    """Personal energy-target inputs from the config file's `profile:` mapping (only known keys)."""
    raw = _config().get("profile") or {}
    return {k: raw[k] for k in _PROFILE_KEYS if isinstance(raw, dict) and k in raw}


def weight_unit() -> str:
    """Display unit for weight, 'kg' or 'lb'. Stored data is always kg."""
    value = (os.environ.get("HEALTH_INSIGHTS_WEIGHT_UNIT") or _config().get("weight_unit") or "kg").strip().lower()
    return value if value in ("kg", "lb") else "kg"


def reset() -> None:
    _config.cache_clear()
    timezone_name.cache_clear()
