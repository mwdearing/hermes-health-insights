"""Opt-in modules: optional checks and skills (medication adherence, GLP-1 monitoring, ...) that are OFF until the user turns them on.

Turn them on or off with `health-insights modules enable|disable ID` (writes ~/.config/health-insights/modules.yaml),
by hand in that file or in config.yaml (`modules: {id: true}`), or for one run with HEALTH_INSIGHTS_MODULES=id1,id2.
See docs/MODULES.md for how to add a module.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from . import settings
from health_insights.sqlite_ro import connect_readonly


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    summary: str            # one plain-language sentence: what turning it on does
    category: str           # e.g. "medication", "body", "heart"
    data_needs: tuple = ()  # Apple Health / receiver type_codes it works best with
    rules: tuple = ()       # ((name, callable(db_path, ref_date) -> [Finding]), ...) added to `concerns`
    skills: tuple = ()      # plugin skills that explain and use it
    cautions: tuple = ("Informational only, not medical advice.",)
    red_flags: tuple = ()   # symptoms the user should act on regardless of any data (shown by `modules info`)
    report: Callable | None = None   # optional (db_path, ref_date) -> list[str] for `modules report`
    default_enabled: bool = False   # optional modules are always off until the user opts in


REGISTRY: dict[str, Module] = {}


def register(module: Module) -> Module:
    REGISTRY[module.id] = module
    return module


def get(module_id: str) -> Module:
    try:
        return REGISTRY[module_id]
    except KeyError:
        raise KeyError(f"unknown module {module_id!r}; known: {', '.join(sorted(REGISTRY)) or 'none'}") from None


def _entry(module_id: str):
    return settings.module_settings().get(module_id)


def is_enabled(module_id: str) -> bool:
    module = get(module_id)
    value = _entry(module_id)
    if isinstance(value, dict):
        return bool(value.get("enabled", False))
    if value is not None:
        return bool(value)
    if module_id == "medication_adherence" and settings.medlog_enabled():
        return True  # legacy switch: integrations.medlog / HEALTH_INSIGHTS_MEDLOG
    return module.default_enabled


def options(module_id: str) -> dict:
    get(module_id)
    value = _entry(module_id)
    opts = value.get("options") if isinstance(value, dict) else None
    return dict(opts) if isinstance(opts, dict) else {}


def set_enabled(module_id: str, enabled: bool) -> Path:
    """Write the switch to modules.yaml, keeping any options already set. Returns the file path."""
    get(module_id)
    path = settings.modules_file()
    data = dict(settings._modules_file_data())
    current = data.get(module_id)
    if isinstance(current, dict):
        current = dict(current)
        current["enabled"] = enabled
        data[module_id] = current
    else:
        data[module_id] = enabled
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".modules-")
    with os.fdopen(fd, "w") as fh:
        fh.write("# Managed by `health-insights modules`. true = on, false = off. Safe to edit by hand.\n")
        yaml.safe_dump(data, fh, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    settings.reset()
    return path


_DOSING = re.compile(
    r"\b(increase|decrease|reduce|raise|lower|skip\w*|double|halve|titrat\w*|up-?titrat\w*|down-?titrat\w*)\b[^.]{0,30}"
    r"\b(dose|doses|dosage|medication|injection)\b|\btitrat\w*\b|\bdouble the dose\b", re.IGNORECASE)


def lint_advice(text: str) -> list[str]:
    """Phrases in module output that read as dosing or medication-change advice (modules must never contain any)."""
    return [m.group(0) for m in _DOSING.finditer(text or "")]


def module_rules() -> list[tuple[str, Callable]]:
    """Concern rules contributed by ENABLED modules only."""
    rules: list[tuple[str, Callable]] = []
    for module in REGISTRY.values():
        if module.rules and is_enabled(module.id):
            rules.extend(module.rules)
    return rules


def readiness(module_id: str, db_path: str) -> dict:
    """Which of the module's data types exist in the database (counts nothing else)."""
    module = get(module_id)
    present: list[str] = []
    try:
        conn = connect_readonly(db_path)
        have = {row[0] for row in conn.execute("SELECT DISTINCT type_code FROM samples")}
        conn.close()
    except sqlite3.Error:
        have = set()
    present = [t for t in module.data_needs if t in have]
    return {"present": present, "missing": [t for t in module.data_needs if t not in have]}


register(Module(
    id="medication_adherence",
    title="Medication adherence",
    summary="needs the medlog CLI (not included) to flag repeatedly missed doses (counts only, never names or doses).",
    category="medication",
    data_needs=(),
    rules=(),  # the rule itself lives in the core list and checks this switch
    skills=(),
    cautions=("Needs the separate `medlog` command line tool.", "Counts only: no medication names, doses or dosing advice."),
))


from .concern_rules import glp1_rules  # noqa: E402,F401  (registers the glp1 module)


try:  # deployment-only module: its file is left out of the public export, so the import is optional
    import importlib
    importlib.import_module(f"{__package__}.weekly_injection_window")  # registers weekly_injection_window
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}.weekly_injection_window":
        raise
