"""Live-DB tests for BUG-PIPELINE-SYNTHETIC-FILTER-001.

Schema assertions, apply_classification round-trips, boot_briefing
view exposure, backfill verification. All gated on DATABASE_URL —
skip silently in CI without secrets.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


@pytestmark_integration
def test_rejected_at_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='rejected_at'"
            )
            r = cur.fetchone()
    assert r is not None, "rejected_at column missing"
    assert r[0] == "timestamp with time zone"
    assert r[1] == "YES", "should be nullable"


@pytestmark_integration
def test_rejected_by_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='rejected_by'"
            )
            r = cur.fetchone()
    assert r is not None, "rejected_by column missing"
    assert r[0] == "text"
    assert r[1] == "YES", "should be nullable"
