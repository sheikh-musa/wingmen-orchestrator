"""Tests for nervous_system.deploy_verifier (ORCHESTRATOR-STATUS-001 Option B)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import uuid

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)
# Apply via @pytestmark_integration on individual tests that hit live DB.
# Pure-unit tests (parser logic, mock-based state machine) skip the decorator
# so they run without DATABASE_URL set (CI-safe).
# Pattern matches tests/test_auto_agent_id.py + tests/test_repo_context_writer.py.


@pytestmark_integration
def test_bug_reports_has_option_b_columns():
    """AC-B-7 part 1: 5 new columns on bug_reports for verifier state."""
    expected = {
        "verified_at": ("timestamp with time zone", "YES"),
        "verification_started_at": ("timestamp with time zone", "YES"),
        "verification_diagnostic": ("text", "YES"),
        "manual_override_reason": ("text", "YES"),
        "verification_escalated_at": ("timestamp with time zone", "YES"),
    }
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'bug_reports'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"


@pytestmark_integration
def test_bug_reports_status_check_accepts_pr_open():
    """AC-B-9: bug_reports.status CHECK must accept new states pr_open / push_failed / pr_failed.

    INSERTs a row with status='pr_open' inside an explicit transaction, then rolls
    back so no test row is committed. Pre-migration this raises CheckViolation.
    """
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bug_reports
                  (id, reporter_name, reporter_source, repo_name, description, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), "test", "telegram", "cosem-tdu", "test bug", "pr_open"),
            )
        c.rollback()
