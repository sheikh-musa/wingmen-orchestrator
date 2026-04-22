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
