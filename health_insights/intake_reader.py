"""Read effective intake components from the receiver's intake-context store.

Connects intake_revisions to intake_compound_facts, selects only the newest
revision per (owner_id, producer_id, intake_id), excludes tombstoned and
deleted intakes, and returns a flat list of IntakeComponent dataclasses.
Always read-only — uses the connection the caller gives it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IntakeComponent:
    owner_id: str
    producer_id: str
    intake_id: str
    revision: int
    occurred_at: str
    time_zone: str
    category: str
    display_name: str
    component_id: str
    position: int
    kind: str
    code: str
    value_state: str
    amount: Optional[str]
    unit: Optional[str]
    quantity_basis: Optional[str]
    aggregation_role: str
    provenance: str
    label_name: Optional[str]


# Columns we need from intake_revisions + intake_compound_facts
_REVISION_COLS = (
    "r.owner_id, r.producer_id, r.intake_id, r.revision, r.occurred_at, "
    "r.time_zone, r.category, r.display_name"
)
_FACT_COLS = (
    "f.component_id, f.position, f.kind, f.code, f.value_state, "
    "f.amount, f.unit, f.quantity_basis, f.aggregation_role, "
    "f.provenance, f.label_name"
)

_SQL = f"""
SELECT {_REVISION_COLS}, {_FACT_COLS}
FROM intake_revisions r
JOIN intake_compound_facts f ON f.intake_revision_row_id = r.intake_revision_row_id
WHERE (r.owner_id, r.producer_id, r.intake_id) NOT IN (
    SELECT t.owner_id, t.producer_id, t.intake_id FROM intake_tombstones t
)
AND (
    r.owner_id, r.producer_id, r.intake_id
) NOT IN (
    SELECT s.owner_id, s.producer_id, s.intake_id
    FROM intake_state s WHERE s.deleted = 1
)
AND r.revision = (
    SELECT MAX(r2.revision)
    FROM intake_revisions r2
    WHERE r2.owner_id = r.owner_id
      AND r2.producer_id = r.producer_id
      AND r2.intake_id = r.intake_id
)
ORDER BY r.occurred_at, r.intake_id, f.position
"""


def _row_to_component(row: tuple) -> IntakeComponent:
    return IntakeComponent(
        owner_id=row[0],
        producer_id=row[1],
        intake_id=row[2],
        revision=row[3],
        occurred_at=row[4],
        time_zone=row[5],
        category=row[6],
        display_name=row[7],
        component_id=row[8],
        position=row[9],
        kind=row[10],
        code=row[11],
        value_state=row[12],
        amount=row[13],
        unit=row[14],
        quantity_basis=row[15],
        aggregation_role=row[16],
        provenance=row[17],
        label_name=row[18],
    )


def read_effective_components(
    conn: sqlite3.Connection,
    owner_id: Optional[str] = None,
) -> list[IntakeComponent]:
    """Return effective (newest-revision, non-tombstoned, non-deleted) components.

    If the intake tables do not exist (older receiver DB) returns [] instead of
    raising.  Read-only — no INSERT/UPDATE/DELETE/CREATE.
    """
    try:
        cur = conn.cursor()
        if owner_id:
            cur.execute(_SQL + " AND r.owner_id = ?", (owner_id,))
        else:
            cur.execute(_SQL)
        return [_row_to_component(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
