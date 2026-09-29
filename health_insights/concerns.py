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


def worst_level(findings: list[Finding]) -> int:
    """Return the highest level among *findings*, or 0 for none."""
    return max((f.level for f in findings), default=0)


DEFAULT_RULES: list[tuple[str, Callable]] = [
    ("adherence", adherence_rules.adherence_findings),
    ("bp", bp_rules.bp_findings),
    ("data_gaps", gap_rules.data_gap_findings),
    ("labs", labs_rules.labs_findings),
    ("nutrition", nutrition_rules.nutrition_gap_findings),
    ("spo2_ecg", spo2_ecg_rules.spo2_ecg_findings),
    ("vitals", vitals_rules.vitals_findings),
    ("workouts", workout_rules.workout_findings),
]


def evaluate(
    db_path: str,
    ref_date_str: str,
    rules: Optional[list[tuple[str, Callable]]] = None,
) -> list[Finding]:
    """Run *rules* against *db_path* for *ref_date_str* (YYYY-MM-DD).

    Returns findings sorted by level descending, then id ascending.
    A rule that raises becomes a level-1 error finding; it does NOT
    stop the other rules from running.
    """
    ref_date = Date.fromisoformat(ref_date_str)
    if rules is None:
        from . import modules  # late import: modules imports rule code that imports this module
        rules = list(DEFAULT_RULES) + modules.module_rules()

    all_findings: list[Finding] = []
    for name, fn in rules:
        try:
            findings = fn(db_path, ref_date)
            all_findings.extend(findings)
        except Exception as exc:
            exc_type = type(exc).__name__
            all_findings.append(
                Finding(
                    id=f"{name}_error",
                    level=1,
                    title="A health check failed to run",
                    evidence=f"rule '{name}' failed ({exc_type})",
                    source="internal",
                    advice="This check did not run; the rest of the report is unaffected. Tell Claude.",
                )
            )

    all_findings.sort(key=lambda f: (-f.level, f.id))
    return all_findings
