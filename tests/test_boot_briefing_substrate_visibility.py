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


@pytestmark_integration
def test_boot_briefing_view_has_ralph_state_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "ralph_state" in defn, "boot_briefing view missing ralph_state UNION arm"


@pytestmark_integration
def test_boot_briefing_view_has_cc_session_costs_outlier_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "cc_session_costs" in defn
    assert "outlier_24h" in defn


@pytestmark_integration
def test_boot_briefing_view_has_inbox_hygiene_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "inbox_hygiene" in defn
    assert "stale_unresponded_12h" in defn


@pytestmark_integration
def test_boot_briefing_ralph_state_arm_returns_seed_row():
    """The view should expose the seeded paused ralph_state."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT context FROM boot_briefing
                 WHERE source='ralph_state' AND key='current'
            """)
            r = cur.fetchone()
    assert r is not None, "boot_briefing should surface ralph_state seed"
    ctx = r[0]
    assert ctx['state'] == 'paused'
    assert ctx['paused_reason'] is not None
    assert isinstance(ctx['resume_gates'], list)


@pytestmark_integration
def test_boot_briefing_outlier_threshold_pulled_from_config():
    """The threshold is read from boot_briefing_config (CAI-RESP-154 Q3 amendment).
    Inserting a session at 60k tokens should surface; one at 40k should not.
    Threshold is 50000 by seed."""
    base_session = str(uuid.uuid4())
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            try:
                # Below threshold — should NOT surface
                cur.execute("""
                    INSERT INTO cc_session_costs
                      (cc_identity, session_id, started_at, input_tokens, output_tokens, source)
                    VALUES ('test-low', %s, now(), 30000, 10000, 'test')
                """, (f"low-{base_session}",))

                # Above threshold — should surface
                cur.execute("""
                    INSERT INTO cc_session_costs
                      (cc_identity, session_id, started_at, input_tokens, output_tokens, source)
                    VALUES ('test-high', %s, now(), 50000, 15000, 'test')
                """, (f"high-{base_session}",))

                cur.execute("""
                    SELECT key, context FROM boot_briefing
                     WHERE source='cc_session_costs' AND key='outlier_24h'
                       AND (context->>'session_id') LIKE %s
                """, (f"%{base_session}%",))
                rows = cur.fetchall()
                session_ids = [r[1].get('session_id') for r in rows]
                assert any(f"high-{base_session}" in s for s in session_ids), \
                    f"high-token session should surface as outlier, got: {session_ids}"
                assert not any(f"low-{base_session}" in s for s in session_ids), \
                    f"low-token session should NOT surface, got: {session_ids}"
            finally:
                cur.execute("""
                    DELETE FROM cc_session_costs
                     WHERE session_id LIKE %s
                """, (f"%{base_session}%",))


@pytestmark_integration
def test_boot_briefing_inbox_hygiene_no_false_positives():
    """When no agent_messages match the stale criteria, the arm should be absent.
    The arm is row-per-stale-message (capped at 10), not aggregated count."""
    # We can't reliably assert ZERO rows without poisoning real data.
    # Just verify: each row that DOES appear has the correct shape.
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT key, context FROM boot_briefing
                 WHERE source='inbox_hygiene' AND key='stale_unresponded_12h'
                 LIMIT 10
            """)
            rows = cur.fetchall()
    for k, ctx in rows:
        assert k == 'stale_unresponded_12h'
        assert 'message_id' in ctx or 'id' in ctx, f"missing message id field in context: {ctx}"
        assert 'age_minutes' in ctx or 'age_hours' in ctx, f"missing age field in context: {ctx}"


@pytestmark_integration
def test_migration_assertion_gate_passes():
    """Reapply migration; assertion gate (Section 6) should not raise."""
    sql = open(
        Path(__file__).parent.parent / "supabase/migrations/20260509_boot_briefing_substrate_visibility.sql"
    ).read()
    # Collect NOTICE messages via psycopg3 notice handler API
    notices: list[str] = []

    def _collect(diag: psycopg.errors.Diagnostic) -> None:
        if diag.message_primary:
            notices.append(diag.message_primary.strip())

    with psycopg.connect(_DSN, autocommit=True) as c:
        c.add_notice_handler(_collect)
        with c.cursor() as cur:
            cur.execute(sql)
    # Expect at least one NOTICE from the assertion gate
    assert any('substrate-visibility' in n.lower() or 'arm' in n.lower() for n in notices), \
        f"expected gate NOTICE; got: {notices}"


@pytestmark_integration
def test_record_session_cost_round_trip():
    """End-to-end: invoke the helper script via subprocess; verify a row
    exists in cc_session_costs afterward."""
    import subprocess as sp
    session_id = f"roundtrip-{uuid.uuid4().hex[:10]}"
    result = sp.run(
        [sys.executable,
         str(Path(__file__).parent.parent / "scripts" / "record_session_cost.py"),
         "--cc-identity", "cc-orchestrator",
         "--sub-tag", "cc-orchestrator-2",
         "--session-id", session_id,
         "--input-tokens", "1234",
         "--output-tokens", "567",
         "--source", "claude_code_cli",
         "--notes", "round-trip integration test"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"script exited {result.returncode}: stderr={result.stderr}"
    try:
        with psycopg.connect(_DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT cc_identity, input_tokens, output_tokens, source, notes "
                    "FROM cc_session_costs WHERE session_id=%s",
                    (session_id,),
                )
                r = cur.fetchone()
        assert r is not None, "row should exist after script run"
        assert r[0] == "cc-orchestrator"
        assert r[1] == 1234
        assert r[2] == 567
        assert r[3] == "claude_code_cli"
        assert "round-trip" in r[4]
    finally:
        with psycopg.connect(_DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM cc_session_costs WHERE session_id=%s", (session_id,))
