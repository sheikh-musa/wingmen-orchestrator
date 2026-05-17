"""Live-DB tests for CC-LONG-CALLER-REGISTRY-001 Phase A.

Schema, view extension, helper round-trip, substrate-native seed.
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

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


def _column_exists(table: str, column: str):
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s",
                (table, column),
            )
            return cur.fetchone()


@pytestmark_integration
def test_long_running_claude_callers_table_exists():
    assert _column_exists("long_running_claude_callers", "caller_name") is not None


@pytestmark_integration
def test_long_running_claude_callers_has_all_fields():
    expected = {
        "caller_name": "text",
        "cmd": "text",
        "parent_pid": "integer",
        "started_at": "timestamp with time zone",
        "expected_cadence_seconds": "integer",
        "expected_tokens_per_day": "integer",
        "max_tokens_per_day": "integer",
        "ratified_by_decision_ref": "text",
        "last_seen_at": "timestamp with time zone",
        "operator_authored": "boolean",
        "registered_by_identity": "text",
        "auto_kill_policy": "text",
        "purpose": "text",
        "revoked_at": "timestamp with time zone",
        "created_at": "timestamp with time zone",
    }
    for col, dtype in expected.items():
        r = _column_exists("long_running_claude_callers", col)
        assert r is not None, f"long_running_claude_callers.{col} missing"
        assert r[0] == dtype, f"{col} has {r[0]!r}, expected {dtype!r}"


@pytestmark_integration
def test_caller_name_is_primary_key():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT a.attname
                  FROM pg_index i
                  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                 WHERE i.indrelid = 'long_running_claude_callers'::regclass
                   AND i.indisprimary
            """)
            pk_cols = [r[0] for r in cur.fetchall()]
    assert pk_cols == ["caller_name"], f"PK should be caller_name, got {pk_cols}"


@pytestmark_integration
def test_registered_by_identity_check_constraint():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO long_running_claude_callers
                      (caller_name, cmd, started_at, expected_cadence_seconds,
                       expected_tokens_per_day, ratified_by_decision_ref,
                       registered_by_identity, auto_kill_policy, purpose)
                    VALUES (%s, 'fake', now(), 300, 1000, 'TEST-FAKE',
                            'invalid_identity_value', 'soft_alert', 'test fixture')
                """, (f"test-fixture-{uuid.uuid4().hex[:8]}",))
                assert False, "registered_by_identity CHECK should have rejected"
            except psycopg.errors.CheckViolation:
                pass


@pytestmark_integration
def test_auto_kill_policy_check_constraint():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO long_running_claude_callers
                      (caller_name, cmd, started_at, expected_cadence_seconds,
                       expected_tokens_per_day, ratified_by_decision_ref,
                       registered_by_identity, auto_kill_policy, purpose)
                    VALUES (%s, 'fake', now(), 300, 1000, 'TEST-FAKE',
                            'operator', 'invalid_policy', 'test')
                """, (f"test-fixture-{uuid.uuid4().hex[:8]}",))
                assert False, "auto_kill_policy CHECK should have rejected"
            except psycopg.errors.CheckViolation:
                pass


@pytestmark_integration
def test_substrate_native_seed_present():
    """ralphy + paused-job-retry seeded at migration time."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT caller_name, registered_by_identity, auto_kill_policy
                  FROM long_running_claude_callers
                 WHERE registered_by_identity = 'substrate'
                 ORDER BY caller_name
            """)
            rows = cur.fetchall()
    caller_names = [r[0] for r in rows]
    assert "ralphy" in caller_names, f"ralphy seed missing, got {caller_names}"
    assert "paused-job-retry" in caller_names, f"paused-job-retry seed missing, got {caller_names}"
    for r in rows:
        assert r[1] == "substrate"
        assert r[2] == "no_kill", f"{r[0]} substrate seed must have auto_kill_policy=no_kill"


@pytestmark_integration
def test_boot_briefing_view_has_long_running_caller_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "long_running_caller" in defn, "boot_briefing view missing long_running_caller UNION arm"
