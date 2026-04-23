"""Batch 1 Structural Integrity Bundle — integration tests.

Covers BUG-024 Phase 1B + Phase 1C (BUG-032) + BUG-031 + BUG-029 Part A.
Requires DATABASE_URL or SUPABASE_DB_URL; migration must be applied.
"""
import os
from pathlib import Path

import psycopg
import pytest

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "supabase/migrations/20260424_batch1_structural_integrity.sql"
)


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_agent_status_has_base_agent_id_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_status' AND column_name = 'base_agent_id'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "text"
        assert row[1] == "NO"  # NOT NULL after backfill


def test_agent_status_base_agent_id_backfilled():
    """Existing rows should have base_agent_id derived from agent_id pattern."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id, base_agent_id FROM agent_status ORDER BY agent_id")
        import re
        for agent_id, base in cur.fetchall():
            expected = re.sub(r'-[0-9]+$', '', agent_id)
            assert base == expected, f"{agent_id} → base={base}, expected {expected}"


def test_agent_status_base_agent_id_fk_enforced():
    """FK to agents(id) rejects non-existent family."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                """
                INSERT INTO agent_status (agent_id, base_agent_id, status)
                VALUES ('test-bogus-9', 'cc-nonexistent', 'offline')
                """
            )


def test_agent_status_check_constraint_rejects_prefix_mismatch():
    """CAI-RESP-080 Open Q2: FK alone allows cross-family; CHECK enforces prefix match."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO agent_status (agent_id, base_agent_id, status)
                VALUES ('cc-ihsanos-999', 'cc-scholar', 'offline')
                """
            )
        # Accept case: matching prefix — verify INSERT then cleanup
        cur.execute(
            """
            INSERT INTO agent_status (agent_id, base_agent_id, status)
            VALUES ('cc-ihsanos-999', 'cc-ihsanos', 'offline')
            """
        )
        cur.execute("DELETE FROM agent_status WHERE agent_id = 'cc-ihsanos-999'")


def test_agent_status_insert_with_base_agent_id_succeeds():
    """Positive path: matching family inserts cleanly."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_status (agent_id, base_agent_id, status)
            VALUES ('cc-scholar-99', 'cc-scholar', 'offline')
            RETURNING base_agent_id
            """
        )
        ret = cur.fetchone()[0]
        try:
            assert ret == 'cc-scholar'
        finally:
            cur.execute("DELETE FROM agent_status WHERE agent_id = 'cc-scholar-99'")


def test_strategic_decisions_posted_by_identity_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'strategic_decisions' AND column_name = 'posted_by_identity'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "text"
        assert row[1] == "YES"


def test_strategic_decisions_decided_by_verified_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'strategic_decisions' AND column_name = 'decided_by_verified'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "boolean"
        assert row[1] == "YES"
