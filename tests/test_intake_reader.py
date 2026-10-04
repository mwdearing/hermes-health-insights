"""Tests for health_insights.intake_reader."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from health_insights.intake_reader import IntakeComponent, read_effective_components


def _make_db() -> tuple[Path, sqlite3.Connection]:
    """Create a minimal DB with the intake tables."""
    path = Path(tempfile.mkdtemp()) / "test.sqlite"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE intake_producers (
            intake_producer_row_id INTEGER PRIMARY KEY,
            owner_id TEXT NOT NULL,
            producer_id TEXT NOT NULL,
            writer_bundle_id TEXT NOT NULL,
            display_label TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(owner_id, producer_id)
        );
        CREATE TABLE intake_state (
            owner_id TEXT NOT NULL,
            producer_id TEXT NOT NULL,
            intake_id TEXT NOT NULL,
            current_revision INTEGER NOT NULL,
            current_projection_sequence INTEGER NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1)),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(owner_id, producer_id, intake_id)
        );
        CREATE TABLE intake_revisions (
            intake_revision_row_id INTEGER PRIMARY KEY,
            owner_id TEXT NOT NULL,
            producer_id TEXT NOT NULL,
            intake_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            domain_facts_hash TEXT NOT NULL,
            projection_hash TEXT NOT NULL,
            client_payload_hash TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            time_zone TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            category TEXT NOT NULL,
            display_name TEXT NOT NULL,
            serving_amount TEXT NOT NULL,
            serving_unit TEXT NOT NULL,
            nutrition_completeness TEXT NOT NULL,
            received_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(owner_id, producer_id, intake_id, revision)
        );
        CREATE TABLE intake_compound_facts (
            intake_fact_row_id INTEGER PRIMARY KEY,
            intake_revision_row_id INTEGER NOT NULL
                REFERENCES intake_revisions(intake_revision_row_id),
            component_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('nutrient', 'compound', 'blend')),
            code TEXT NOT NULL,
            label_name TEXT,
            value_state TEXT NOT NULL CHECK(value_state IN ('known', 'unknown', 'not_applicable', 'below_reporting_threshold')),
            amount TEXT,
            unit TEXT,
            quantity_basis TEXT,
            aggregation_role TEXT NOT NULL CHECK(aggregation_role IN ('context_only', 'compound_measurement', 'blend_total_only')),
            provenance TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(intake_revision_row_id, component_id),
            UNIQUE(intake_revision_row_id, position)
        );
        CREATE TABLE intake_tombstones (
            intake_tombstone_row_id INTEGER PRIMARY KEY,
            owner_id TEXT NOT NULL,
            producer_id TEXT NOT NULL,
            intake_id TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            revision INTEGER NOT NULL,
            operation_id TEXT NOT NULL,
            domain_facts_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(owner_id, producer_id, intake_id)
        );
    """)
    conn.commit()
    return path, conn


class TestNewestRevisionWins:
    """Only the newest revision per (owner, producer, intake) should appear."""

    def test_latest_revision_selected(self):
        path, conn = _make_db()
        try:
            # Insert producer
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'nutrition-app', 'dev.example.nutrition', 'App', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            # Revision 1
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'nutrition-app', 'intake-a', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test Drink', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '3', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-a', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            # Revision 2 (supersedes)
            r2 = conn.execute(
                "INSERT INTO intake_revisions VALUES (2, 'owner-1', 'nutrition-app', 'intake-a', 2, 'sha256:ddd', 'sha256:eee', 'sha256:fff', 'inst-1', 'op-2', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T13:00:00Z', 'drink', 'Test Drink', '1', 'serving', 'complete', '2026-10-01T13:00:00Z', '2026-10-01T13:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (2, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '5', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T13:00:00Z')",
                (r2,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-a', 2, 1, 0, '2026-10-01T13:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            assert len(comps) == 1
            assert comps[0].amount == "5"
            assert comps[0].revision == 2
        finally:
            conn.close()
            path.unlink(missing_ok=True)


class TestTombstonedExcluded:
    """Intakes with a tombstone row should be excluded."""

    def test_tombstoned_intake_excluded(self):
        path, conn = _make_db()
        try:
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'nutrition-app', 'dev.example.nutrition', 'App', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            # Normal intake
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'nutrition-app', 'intake-ok', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '3', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-ok', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            # Tombstoned intake
            r2 = conn.execute(
                "INSERT INTO intake_revisions VALUES (2, 'owner-1', 'nutrition-app', 'intake-del', 1, 'sha256:ddd', 'sha256:eee', 'sha256:fff', 'inst-1', 'op-2', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (2, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '100', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r2,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-del', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            conn.execute(
                "INSERT INTO intake_tombstones VALUES (1, 'owner-1', 'nutrition-app', 'intake-del', '2026-10-01T14:00:00Z', 1, 'op-del', 'sha256:ggg', '2026-10-01T14:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            assert all(c.intake_id != "intake-del" for c in comps)
            assert any(c.intake_id == "intake-ok" for c in comps)
        finally:
            conn.close()
            path.unlink(missing_ok=True)


class TestDeletedStateExcluded:
    """Intakes with intake_state.deleted = 1 should be excluded."""

    def test_deleted_intake_excluded(self):
        path, conn = _make_db()
        try:
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'nutrition-app', 'dev.example.nutrition', 'App', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            # Deleted intake
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'nutrition-app', 'intake-del', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '100', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-del', 1, 1, 1, '2026-10-01T12:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            assert len(comps) == 0
        finally:
            conn.close()
            path.unlink(missing_ok=True)


class TestProducerIdKept:
    """producer_id must be preserved from intake_revisions."""

    def test_producer_id_preserved(self):
        path, conn = _make_db()
        try:
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'nutrition-app', 'dev.example.nutrition', 'App', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO intake_producers VALUES (2, 'owner-1', 'other-app', 'dev.example.other', 'Other', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            # nutrition-app
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'nutrition-app', 'intake-a', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '3', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-a', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            # other-app
            r2 = conn.execute(
                "INSERT INTO intake_revisions VALUES (2, 'owner-1', 'other-app', 'intake-b', 1, 'sha256:ddd', 'sha256:eee', 'sha256:fff', 'inst-1', 'op-2', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (2, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '2', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r2,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'other-app', 'intake-b', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            producers = {c.producer_id for c in comps}
            assert producers == {"nutrition-app", "other-app"}
        finally:
            conn.close()
            path.unlink(missing_ok=True)


class TestTombstoneScopedByOwnerAndProducer:
    """Tombstones must match on (owner_id, producer_id, intake_id), not intake_id alone.

    See RD-01: a tombstone from one producer (or another owner) must not
    hide a different producer's intake that happens to share the same intake_id.
    """

    def test_tombstone_for_one_producer_hides_only_that_producer(self):
        """Two producers share the same intake_id; tombstone for app-a must not hide app-b."""
        path, conn = _make_db()
        try:
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'app-a', 'dev.example.app-a', 'AppA', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO intake_producers VALUES (2, 'owner-1', 'app-b', 'dev.example.app-b', 'AppB', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            # Both producers record the same intake_id 'shared'
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'app-a', 'shared', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Shared', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '3', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'app-a', 'shared', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            r2 = conn.execute(
                "INSERT INTO intake_revisions VALUES (2, 'owner-1', 'app-b', 'shared', 1, 'sha256:ddd', 'sha256:eee', 'sha256:fff', 'inst-1', 'op-2', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Shared', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (2, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '5', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r2,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'app-b', 'shared', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            # Tombstone for app-a only
            conn.execute(
                "INSERT INTO intake_tombstones VALUES (1, 'owner-1', 'app-a', 'shared', '2026-10-01T14:00:00Z', 1, 'op-del', 'sha256:ggg', '2026-10-01T14:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            producers = {c.producer_id for c in comps}
            assert producers == {"app-b"}, f"Expected app-b only, got {producers}"

        finally:
            conn.close()
            path.unlink(missing_ok=True)

    def test_tombstone_from_different_owner_hides_nothing(self):
        """A tombstone recorded under a different owner_id must not hide any intake."""
        path, conn = _make_db()
        try:
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'app-a', 'dev.example.app-a', 'AppA', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'app-a', 'shared', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Shared', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', NULL, 'known', '3', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'app-a', 'shared', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            # Tombstone for a DIFFERENT owner with the same producer+intake_id
            conn.execute(
                "INSERT INTO intake_tombstones VALUES (1, 'other-owner', 'app-a', 'shared', '2026-10-01T14:00:00Z', 1, 'op-del', 'sha256:ggg', '2026-10-01T14:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            assert len(comps) == 1, f"Expected 1 component, got {len(comps)}"
            assert comps[0].producer_id == "app-a"

        finally:
            conn.close()
            path.unlink(missing_ok=True)


class TestMissingTablesGraceful:
    """If intake tables don't exist, return [] instead of raising."""

    def test_no_intake_tables_returns_empty(self):
        path = Path(tempfile.mkdtemp()) / "old.sqlite"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE samples (sample_id INTEGER PRIMARY KEY)")
            conn.commit()
            result = read_effective_components(conn)
            assert result == []
        finally:
            conn.close()
            path.unlink(missing_ok=True)


class TestRequiredFields:
    """IntakeComponent must have all required fields."""

    def test_all_fields_present(self):
        path, conn = _make_db()
        try:
            conn.execute(
                "INSERT INTO intake_producers VALUES (1, 'owner-1', 'nutrition-app', 'dev.example.nutrition', 'App', '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            r1 = conn.execute(
                "INSERT INTO intake_revisions VALUES (1, 'owner-1', 'nutrition-app', 'intake-a', 1, 'sha256:aaa', 'sha256:bbb', 'sha256:ccc', 'inst-1', 'op-1', '2026-10-01T07:00:00-05:00', 'America/Chicago', '2026-10-01T12:00:00Z', 'drink', 'Test', '1', 'serving', 'complete', '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z')"
            ).lastrowid
            conn.execute(
                "INSERT INTO intake_compound_facts VALUES (1, ?, 'c1', 0, 'compound', 'creatine', 'Creatine', 'known', '5', 'g', 'compound_mass', 'compound_measurement', 'user_confirmed', '2026-10-01T12:00:00Z')",
                (r1,),
            )
            conn.execute(
                "INSERT INTO intake_state VALUES ('owner-1', 'nutrition-app', 'intake-a', 1, 1, 0, '2026-10-01T12:00:00Z')"
            )
            conn.commit()

            comps = read_effective_components(conn)
            assert len(comps) == 1
            c = comps[0]
            for field in (
                "owner_id", "producer_id", "intake_id", "revision",
                "component_id", "kind", "code", "value_state",
                "amount", "unit", "quantity_basis", "provenance",
                "occurred_at", "time_zone", "label_name",
            ):
                assert hasattr(c, field), f"Missing field: {field}"
        finally:
            conn.close()
            path.unlink(missing_ok=True)
