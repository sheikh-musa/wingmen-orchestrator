"""BUG-024 Phase 1: agent_messages provenance layer.

Integration tests against orchestrator Supabase. Require DATABASE_URL or
SUPABASE_DB_URL to be set. Migration must already be applied via
`supabase db push` before running these.
"""
import json
import os
from pathlib import Path

import psycopg
import pytest

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "supabase/migrations/20260422_bug024_phase1_agent_messages_provenance.sql"
)


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_posted_by_identity_column_present():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'posted_by_identity'
            """
        )
        row = cur.fetchone()
        assert row is not None, "posted_by_identity column not found"
        assert row[0] == "text"
        assert row[1] == "YES"


def test_from_agent_verified_column_present():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'from_agent_verified'
            """
        )
        row = cur.fetchone()
        assert row is not None, "from_agent_verified column not found"
        assert row[0] == "boolean"
        assert row[1] == "YES"


def test_cai_session_id_column_present_on_agent_messages():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'cai_session_id'
            """
        )
        row = cur.fetchone()
        assert row is not None, "cai_session_id column not found on agent_messages"
        assert row[0] == "text"
        assert row[1] == "YES"


def test_identity_allowlist_table_exists():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type FROM information_schema.columns
             WHERE table_name = 'identity_allowlist'
             ORDER BY ordinal_position
            """
        )
        cols = {r[0]: r[1] for r in cur.fetchall()}
        assert cols.get("posted_by") == "text"
        assert cols.get("allowed_from_agent") == "text"
        assert cols.get("note") == "text"
        assert "created_at" in cols


def test_identity_allowlist_primary_key():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname FROM pg_index i
             JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
             WHERE i.indrelid = 'identity_allowlist'::regclass AND i.indisprimary
             ORDER BY a.attnum
            """
        )
        pk_cols = [r[0] for r in cur.fetchall()]
        assert pk_cols == ["posted_by", "allowed_from_agent"]


def test_trigger_populates_posted_by_identity():
    """INSERT should populate posted_by_identity from current session role."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cc-ihsanos', 'cai', 'update', 'bug024-trigger-test', 'b')
            RETURNING id, posted_by_identity
            """
        )
        mid, pbi = cur.fetchone()
        try:
            assert pbi is not None, "trigger did not populate posted_by_identity"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_trigger_sets_verified_true_on_allowlist_match():
    """When an allowlist row covers (role, from_agent), verified=true."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        role = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO identity_allowlist (posted_by, allowed_from_agent, note)
            VALUES (%s, 'cc-ihsanos', 'bug024-test-row')
            ON CONFLICT (posted_by, allowed_from_agent) DO NOTHING
            """,
            (role,),
        )
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cc-ihsanos', 'cai', 'update', 'bug024-verified-test', 'b')
            RETURNING id, from_agent_verified
            """
        )
        mid, verified = cur.fetchone()
        try:
            assert verified is True
        finally:
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))
            cur.execute(
                "DELETE FROM identity_allowlist WHERE posted_by = %s AND allowed_from_agent = 'cc-ihsanos' AND note = 'bug024-test-row'",
                (role,),
            )


def test_trigger_sets_verified_null_on_no_match():
    """No allowlist row → verified=NULL (unverified)."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cai', 'cc-ihsanos', 'update', 'bug024-unverified-test', 'b')
            RETURNING id, from_agent_verified
            """
        )
        mid, verified = cur.fetchone()
        try:
            assert verified is None, f"expected NULL, got {verified}"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))
