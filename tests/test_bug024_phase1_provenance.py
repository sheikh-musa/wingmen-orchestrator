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


def test_strategic_decisions_cai_session_id_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'strategic_decisions' AND column_name = 'cai_session_id'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "text"
        assert row[1] == "YES"


def test_partial_index_agent_messages_cai_session():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
             WHERE tablename = 'agent_messages'
               AND indexname = 'idx_agent_messages_cai_session'
            """
        )
        row = cur.fetchone()
        assert row is not None, "partial index idx_agent_messages_cai_session missing"
        idx_def = row[0]
        assert "cai_session_id" in idx_def
        assert "from_agent" in idx_def  # partial predicate


def test_partial_index_strategic_decisions_cai_session():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
             WHERE tablename = 'strategic_decisions'
               AND indexname = 'idx_strategic_decisions_cai_session'
            """
        )
        row = cur.fetchone()
        assert row is not None, "partial index idx_strategic_decisions_cai_session missing"
        idx_def = row[0]
        assert "cai_session_id" in idx_def
        assert "claude_ai_session" in idx_def  # partial predicate on source


def test_boot_briefing_includes_recent_decision_text():
    """Recent (< 14d) cai-authored decisions surface decision + reasoning + cai_session_id."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT context
              FROM boot_briefing
             WHERE source = 'active_decision'
               AND (context->>'decided_at')::timestamptz >= now() - interval '14 days'
             LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            pytest.skip("no recent cai-authored decisions in DB to verify shape")
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert "decision" in payload, "decision text field missing in recent-window row"
        assert "reasoning" in payload, "reasoning text field missing in recent-window row"
        assert "cai_session_id" in payload, "cai_session_id field missing"


def test_boot_briefing_stubs_old_decision_text():
    """Decisions > 14d old retain existing fields but do NOT have decision/reasoning text."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT context
              FROM boot_briefing
             WHERE source = 'active_decision'
               AND (context->>'decided_at')::timestamptz < now() - interval '14 days'
             LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            pytest.skip("no > 14d cai decisions in DB to verify stub shape")
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert "title" in payload, "title field should still be present on old rows"
        assert "cai_session_id" in payload, "cai_session_id should be present on all active_decision rows"
        assert "decision" not in payload, "full decision text should NOT be in >14d row"
        assert "reasoning" not in payload, "full reasoning should NOT be in >14d row"


def test_boot_briefing_last_cai_session_row():
    """boot_briefing surfaces last_cai_session section with gap-in-days."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT context FROM boot_briefing
             WHERE source = 'last_cai_session'
            """
        )
        row = cur.fetchone()
        assert row is not None, "boot_briefing missing last_cai_session section"
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert "cai_session_id" in payload
        assert "gap_days" in payload


def test_msg_252_marked_unverified():
    """BUG-024 incident message: msg 252 should have from_agent_verified=false."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT from_agent_verified FROM agent_messages WHERE id = 252
            """
        )
        row = cur.fetchone()
        if row is None:
            pytest.skip("msg 252 not present in this DB (may be local dev)")
        verified = row[0]
        assert verified is False, (
            f"msg 252 (BUG-024 incident) should be from_agent_verified=false, "
            f"got {verified}"
        )


def test_existing_rows_cai_session_id_null():
    """Pre-migration rows should have cai_session_id = NULL (honest pre-tracking marker)."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM agent_messages
             WHERE id < (SELECT coalesce(max(id), 0) FROM agent_messages WHERE created_at > '2026-04-22 00:00:00+00')
               AND cai_session_id IS NOT NULL
            """
        )
        count = cur.fetchone()[0]
        assert count == 0, f"{count} pre-migration rows have non-NULL cai_session_id (should all be NULL)"
