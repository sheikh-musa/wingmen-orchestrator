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
