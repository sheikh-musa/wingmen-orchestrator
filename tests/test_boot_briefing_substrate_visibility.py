"""Live-DB tests for CAI-RESP-154 (boot_briefing substrate visibility).

Schema assertions for: boot_briefing_config, cc_session_costs,
cc_session_messages, ralph_state. Trigger behavior + seed row + view
extension assertions land in subsequent task chunks.
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


def _column_exists(table: str, column: str) -> tuple | None:
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
def test_boot_briefing_config_table_exists():
    r = _column_exists("boot_briefing_config", "key")
    assert r is not None, "boot_briefing_config.key missing"
    assert r[0] == "text"


@pytestmark_integration
def test_boot_briefing_config_has_outlier_threshold_seed():
    """Seed row: key='cc_session_costs_outlier_token_threshold', value_int=50000."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT value_int FROM boot_briefing_config "
                "WHERE key='cc_session_costs_outlier_token_threshold'"
            )
            r = cur.fetchone()
    assert r is not None, "outlier threshold row missing from boot_briefing_config"
    assert r[0] == 50000


@pytestmark_integration
def test_cc_session_costs_table_exists():
    r = _column_exists("cc_session_costs", "cc_identity")
    assert r is not None, "cc_session_costs.cc_identity missing"


@pytestmark_integration
def test_cc_session_costs_has_required_columns():
    """All Q1 fields present per CAI-RESP-154."""
    expected = {
        "cc_identity": "text",
        "sub_tag": "text",
        "session_id": "text",
        "started_at": "timestamp with time zone",
        "ended_at": "timestamp with time zone",
        "input_tokens": "integer",
        "output_tokens": "integer",
        "source": "text",
        "has_per_message_detail": "boolean",
        "notes": "text",
    }
    for col, dtype in expected.items():
        r = _column_exists("cc_session_costs", col)
        assert r is not None, f"cc_session_costs.{col} missing"
        assert r[0] == dtype, f"cc_session_costs.{col} has {r[0]!r}, expected {dtype!r}"


@pytestmark_integration
def test_cc_session_messages_child_table_exists():
    r = _column_exists("cc_session_messages", "session_cost_id")
    assert r is not None, "cc_session_messages.session_cost_id missing"


@pytestmark_integration
def test_cc_session_messages_fk_to_session_costs():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM information_schema.referential_constraints rc
                  JOIN information_schema.table_constraints tc
                    ON rc.constraint_name = tc.constraint_name
                 WHERE tc.table_name = 'cc_session_messages'
                   AND tc.constraint_type = 'FOREIGN KEY'
            """)
            count = cur.fetchone()[0]
    assert count >= 1, "cc_session_messages should have a foreign key to cc_session_costs"


@pytestmark_integration
def test_ralph_state_table_exists():
    r = _column_exists("ralph_state", "state")
    assert r is not None, "ralph_state.state missing"


@pytestmark_integration
def test_ralph_state_has_seed_row():
    """Single-row pattern (id=1). Seed: state='paused', resume_gates with 4 entries."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, state, paused_reason, resume_gates, last_state_change_by, since "
                "FROM ralph_state WHERE id=1"
            )
            r = cur.fetchone()
    assert r is not None, "ralph_state seed row (id=1) missing"
    assert r[1] == "paused"
    assert r[2] is not None  # paused_reason required when paused
    assert isinstance(r[3], list) and len(r[3]) >= 4, f"resume_gates should be a non-empty list, got {r[3]!r}"
    assert r[4] == "operator-musa"
    # since should be 2026-04-29 in Singapore time
    assert r[5].isoformat().startswith("2026-04-2"), f"since should be approximately 2026-04-29, got {r[5]}"


@pytestmark_integration
def test_ralph_state_only_id_1_allowed():
    """CHECK (id = 1) constraint should reject id != 1."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO ralph_state (id, state, since, last_state_change_by, paused_reason) "
                    "VALUES (2, 'active', now(), 'test', NULL)"
                )
                assert False, "id=2 should have been rejected by CHECK constraint"
            except psycopg.errors.CheckViolation:
                pass  # expected


@pytestmark_integration
def test_ralph_state_paused_requires_reason():
    """Trigger should reject INSERT/UPDATE that sets state='paused' with NULL paused_reason."""
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                # Try to update the seed row to paused with NULL reason
                try:
                    cur.execute(
                        "UPDATE ralph_state SET paused_reason=NULL WHERE id=1"
                    )
                    c.rollback()
                    assert False, "UPDATE setting paused_reason=NULL should have been rejected"
                except (psycopg.errors.CheckViolation, psycopg.errors.RaiseException, psycopg.errors.InternalError) as e:
                    pass  # any of these is acceptable rejection signal
            finally:
                c.rollback()
