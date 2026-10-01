"""Health concerns framework.

Frozen dataclass Finding, level icons, worst_level helper,
DEFAULT_RULES registry, and evaluate() that runs rules
with isolation (a raising rule becomes a level-1 error finding).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Callable, Optional

from .concern_rules import adherence_rules
from .concern_rules import bp_rules
from .concern_rules import gap_rules
from .concern_rules import labs_rules
from .concern_rules import nutrition_rules
from .concern_rules import spo2_ecg_rules
from .concern_rules import vitals_rules
from .concern_rules import workout_rules
from health_insights.sqlite_ro import connect_readonly


@dataclass(frozen=True)
class Finding:
    id: str
    level: int
    title: str
    evidence: str
    source: str
    advice: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "evidence": self.evidence,
            "source": self.source,
            "advice": self.advice,
        }


LEVEL_ICONS = {0: "✅", 1: "👀", 2: "⚠️", 3: "🚨"}
LEVEL_WORDS = {0: "all clear", 1: "worth watching", 2: "discuss with a clinician", 3: "urgent"}


def format_line(f: Finding) -> str:
    """One text line for a finding: icon, level word in brackets, title and evidence."""
    icon = LEVEL_ICONS.get(f.level, "?")
    word = LEVEL_WORDS.get(f.level, f"level {f.level}")
    return f"{icon} [{word}] {f.title} — {f.evidence}"


def worst_level(findings: list[Finding]) -> int:
    """Return the highest level among *findings*, or 0 for none."""
    return max((f.level for f in findings), default=0)


DEFAULT_RULES: list[tuple[str, Callable]] = [
    ("adherence", adherence_rules.adherence_findings),
    ("bp", bp_rules.bp_findings),
    ("data_gaps", gap_rules.data_gap_findings),
    ("store_staleness", gap_rules.store_staleness_findings),
    ("labs", labs_rules.labs_findings),
    ("nutrition", nutrition_rules.nutrition_gap_findings),
    ("spo2_ecg", spo2_ecg_rules.spo2_ecg_findings),
    ("vitals", vitals_rules.vitals_findings),
    ("workouts", workout_rules.workout_findings),
]


def _unavailable(evidence: str) -> Finding:
    return Finding(
        id="concerns_unavailable",
        level=2,
        title="Health checks could not run",
        evidence=evidence,
        source="internal",
        advice="Nothing was checked this time. Fix the database path or sync, then run the check again.",
    )


def _open_problem(db_path: str) -> Optional[str]:
    """Return a short reason when *db_path* cannot be opened as a SQLite database, else None."""
    import sqlite3
    from pathlib import Path

    try:
        path = Path(db_path)
        if not path.is_file():
            return f"database not found at {db_path}"
        conn = connect_readonly(path)
        try:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return f"cannot open database at {db_path} ({type(exc).__name__})"
    return None


def evaluate(
    db_path: str,
    ref_date_str: str,
    rules: Optional[list[tuple[str, Callable]]] = None,
) -> list[Finding]:
    """Run *rules* against *db_path* for *ref_date_str* (YYYY-MM-DD).

    Returns findings sorted by level descending, then id ascending.
    A rule that raises becomes a level-1 error finding; it does NOT
    stop the other rules from running. If the database cannot be
    opened, or more than half the rules raise (standard rule set only), the result is exactly one
    level-2 ``concerns_unavailable`` finding so the report never reads as
    an all-clear when nothing ran.
    """
    ref_date = Date.fromisoformat(ref_date_str)
    # The "nothing ran" guards apply to the standard rule set only: callers that pass their
    # own rules (tests, replays) may use rules that never touch the file.
    guard = rules is None
    if rules is None:
        from . import modules  # late import: modules imports rule code that imports this module
        rules = list(DEFAULT_RULES) + modules.module_rules()

    all_findings: list[Finding] = []
    failed = 0
    for name, fn in rules:
        try:
            findings = fn(db_path, ref_date)
            all_findings.extend(findings)
        except Exception as exc:
            failed += 1
            exc_type = type(exc).__name__
            all_findings.append(
                Finding(
                    id=f"{name}_error",
                    level=1,
                    title="A health check failed to run",
                    evidence=f"rule '{name}' failed ({exc_type})",
                    source="internal",
                    advice="This check did not run; the rest of the report is unaffected. Rerun the job or check its log.",
                )
            )

    if guard and failed:
        # An unopenable database is reported as such (with the cause), never as scattered
        # per-rule errors that read like "the rest was fine".
        problem = _open_problem(db_path)
        if problem is not None:
            return [_unavailable(problem)]
        if failed * 2 > len(rules):
            return [_unavailable(f"{failed} of {len(rules)} checks failed to run")]

    all_findings.sort(key=lambda f: (-f.level, f.id))
    return all_findings
