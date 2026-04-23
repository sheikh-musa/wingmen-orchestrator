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
