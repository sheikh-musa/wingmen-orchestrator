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
