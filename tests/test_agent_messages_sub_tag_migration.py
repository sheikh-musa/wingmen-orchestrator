"""B1 migration: agent_messages.sub_tag column, CHECK constraint, partial index."""
import os
import pytest
import psycopg
from pathlib import Path

MIGRATION_PATH = Path(__file__).parent.parent / "supabase/migrations/20260420_agent_messages_sub_tag.sql"


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_sub_tag_column_present():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'sub_tag'
            """
        )
        row = cur.fetchone()
        assert row is not None, "sub_tag column not found"
        assert row[0] == "text"
        assert row[1] == "YES"  # nullable


def test_check_rejects_cross_family_impersonation():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
                VALUES ('cc-ihsanos', 'cai', 'update', 't', 'b', 'cc-scholar-1')
                """
            )


def test_check_accepts_matching_family():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
            VALUES ('cc-ihsanos', 'cai', 'update', 'b1-test-accept', 'b', 'cc-ihsanos-99')
            RETURNING id
            """
        )
        mid = cur.fetchone()[0]
        cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_check_accepts_null():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cai', 'cc-ihsanos', 'update', 'b1-test-null', 'b')
            RETURNING id, sub_tag
            """
        )
        mid, sub_tag = cur.fetchone()
        assert sub_tag is None
        cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_partial_index_exists():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
             WHERE tablename = 'agent_messages' AND indexname = 'idx_agent_messages_sub_tag'
            """
        )
        row = cur.fetchone()
        assert row is not None, "partial index missing"
        assert "sub_tag IS NOT NULL" in row[0]


def test_migration_idempotent():
    sql = MIGRATION_PATH.read_text()
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)  # should succeed even if already applied
        cur.execute(sql)  # running twice = no-op
