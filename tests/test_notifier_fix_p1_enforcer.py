"""NOTIFIER-FIX P1 Fix 2: challenge_window timeout enforcer.

Integration tests against orchestrator Supabase. Require DATABASE_URL or
SUPABASE_DB_URL. Migration must be applied before these tests run.
"""
import os
from pathlib import Path

import psycopg
import pytest

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "supabase/migrations/20260423_notifier_fix_p1_challenge_enforcer.sql"
)


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_challenge_status_check_allows_accepted_by_timeout():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, status, challenge_status, decided_by)
            VALUES
              ('TEST-CHECK-TIMEOUT', 't', 'd', 'r', 'active', 'accepted_by_timeout', 'cc-ihsanos')
            RETURNING decision_ref
            """
        )
        ref = cur.fetchone()[0]
        try:
            assert ref == 'TEST-CHECK-TIMEOUT'
        finally:
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-CHECK-TIMEOUT'")


def test_runtime_config_table_has_enforcer_mode_row():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM orchestrator_runtime_config WHERE key = 'challenge_enforcer_mode'"
        )
        row = cur.fetchone()
        assert row is not None, "runtime_config missing challenge_enforcer_mode row"
        assert row[0] == 'dry_run', f"enforcer should start in dry_run, got {row[0]}"


def test_runtime_config_value_check_rejects_invalid_mode():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO orchestrator_runtime_config (key, value)
                VALUES ('challenge_enforcer_mode', 'garbage_mode')
                """
            )
