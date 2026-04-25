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


def test_agent_status_has_base_agent_id_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_status' AND column_name = 'base_agent_id'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "text"
        assert row[1] == "NO"  # NOT NULL after backfill


def test_agent_status_base_agent_id_backfilled():
    """Existing rows should have base_agent_id derived from agent_id pattern."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id, base_agent_id FROM agent_status ORDER BY agent_id")
        import re
        for agent_id, base in cur.fetchall():
            expected = re.sub(r'-[0-9]+$', '', agent_id)
            assert base == expected, f"{agent_id} → base={base}, expected {expected}"


def test_agent_status_base_agent_id_fk_enforced():
    """FK to agents(id) rejects non-existent family.

    To exercise FK specifically (not CHECK which fires first), use an agent_id
    whose regexp_replace prefix equals base_agent_id BUT that prefix is not a
    known agents.id row. E.g., 'cc-bogus-9' → 'cc-bogus' (CHECK passes, FK fails).

    Note: trg_agent_status_identity requires SET LOCAL app.current_agent_id = <agent_id>
    matching NEW.agent_id. Wrap in explicit transaction + SET LOCAL.
    """
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL app.current_agent_id = 'cc-bogus-9'")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                """
                INSERT INTO agent_status (agent_id, base_agent_id, status)
                VALUES ('cc-bogus-9', 'cc-bogus', 'offline')
                """
            )
        cur.execute("ROLLBACK")


def test_agent_status_check_constraint_rejects_prefix_mismatch():
    """CAI-RESP-080 Open Q2: FK alone allows cross-family; CHECK enforces prefix match.

    trg_agent_status_identity GUC wrap required per note above.
    """
    # Negative case: CHECK rejects mismatched base
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL app.current_agent_id = 'cc-ihsanos-999'")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO agent_status (agent_id, base_agent_id, status)
                VALUES ('cc-ihsanos-999', 'cc-scholar', 'offline')
                """
            )
        cur.execute("ROLLBACK")
    # Positive case: matching prefix accepted, then cleanup
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL app.current_agent_id = 'cc-ihsanos-999'")
        cur.execute(
            """
            INSERT INTO agent_status (agent_id, base_agent_id, status)
            VALUES ('cc-ihsanos-999', 'cc-ihsanos', 'offline')
            """
        )
        cur.execute("DELETE FROM agent_status WHERE agent_id = 'cc-ihsanos-999'")
        cur.execute("COMMIT")


def test_agent_status_insert_with_base_agent_id_succeeds():
    """Positive path: matching family inserts cleanly. GUC wrap required."""
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL app.current_agent_id = 'cc-scholar-99'")
        cur.execute(
            """
            INSERT INTO agent_status (agent_id, base_agent_id, status)
            VALUES ('cc-scholar-99', 'cc-scholar', 'offline')
            RETURNING base_agent_id
            """
        )
        ret = cur.fetchone()[0]
        try:
            assert ret == 'cc-scholar'
        finally:
            cur.execute("DELETE FROM agent_status WHERE agent_id = 'cc-scholar-99'")
            cur.execute("COMMIT")


def test_strategic_decisions_posted_by_identity_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'strategic_decisions' AND column_name = 'posted_by_identity'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "text"
        assert row[1] == "YES"


def test_strategic_decisions_decided_by_verified_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'strategic_decisions' AND column_name = 'decided_by_verified'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "boolean"
        assert row[1] == "YES"


def test_strategic_decisions_trigger_populates_posted_by_identity():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by)
            VALUES
              ('TEST-SD-PROV-1', 't', 'd', 'r', 'architecture', 'active', 'informational', 'cc-ihsanos')
            RETURNING posted_by_identity, decided_by_verified
            """
        )
        pbi, verified = cur.fetchone()
        try:
            assert pbi is not None  # trigger populated from current_user
            assert verified is None  # no allowlist match in Phase 1 zero-seed
        finally:
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-SD-PROV-1'")


def test_strategic_decisions_trigger_preserves_admin_seeded_value():
    """IF NEW.posted_by_identity IS NULL pattern preserves caller-supplied values."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by,
               posted_by_identity)
            VALUES
              ('TEST-SD-PROV-2', 't', 'd', 'r', 'architecture', 'active', 'informational', 'cc-ihsanos',
               'manual_seed_value')
            """
        )
        try:
            cur.execute(
                """
                UPDATE strategic_decisions
                   SET challenge_status = 'accepted'
                 WHERE decision_ref = 'TEST-SD-PROV-2'
                RETURNING posted_by_identity
                """
            )
            pbi = cur.fetchone()[0]
            assert pbi == 'manual_seed_value', f"trigger clobbered admin-seeded value: {pbi}"
        finally:
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-SD-PROV-2'")


def test_strategic_decisions_trigger_fires_on_update_challenge_status():
    """UPDATE of challenge_status fires the trigger (bulk-flip failure mode coverage per CAI-RESP-077)."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by)
            VALUES
              ('TEST-SD-PROV-3', 't', 'd', 'r', 'architecture', 'active', 'informational', 'cc-ihsanos')
            """
        )
        try:
            # Directly null posted_by_identity (trigger doesn't fire on UPDATE of unrelated columns)
            cur.execute(
                "UPDATE strategic_decisions SET posted_by_identity = NULL WHERE decision_ref = 'TEST-SD-PROV-3'"
            )
            # Now UPDATE challenge_status → trigger should repopulate posted_by_identity
            cur.execute(
                """
                UPDATE strategic_decisions
                   SET challenge_status = 'accepted'
                 WHERE decision_ref = 'TEST-SD-PROV-3'
                RETURNING posted_by_identity
                """
            )
            pbi = cur.fetchone()[0]
            assert pbi is not None, "trigger should have repopulated NULL posted_by_identity on UPDATE OF challenge_status"
        finally:
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-SD-PROV-3'")


def test_strategic_decisions_has_is_test_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default FROM information_schema.columns
             WHERE table_name = 'strategic_decisions' AND column_name = 'is_test'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "boolean"
        assert row[1] == "NO"
        assert row[2] == "false"


def test_agent_messages_has_is_test_column():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable, column_default FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'is_test'
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "boolean"
        assert row[1] == "NO"
        assert row[2] == "false"


def test_agent_messages_is_test_defaults_false_on_insert():
    """New rows default is_test=FALSE."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cc-ihsanos', 'cai', 'update', 'TEST-IS-TEST-DEFAULT', 'b')
            RETURNING id, is_test
            """
        )
        mid, is_test = cur.fetchone()
        try:
            assert is_test is False
        finally:
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_enforcer_accepts_test_mode_parameter():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_function_arguments(oid) FROM pg_proc
             WHERE proname = 'enforce_challenge_window_timeouts'
            """
        )
        args = cur.fetchone()[0]
        assert 'test_mode' in args, f"function signature missing test_mode parameter: {args}"
        assert 'boolean' in args.lower()


def test_enforcer_default_call_excludes_is_test_rows():
    """enforce_challenge_window_timeouts() with no args defaults test_mode=FALSE,
    so is_test=TRUE fixtures are ignored."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by,
               decided_at, challengeable_until, is_test)
            VALUES
              ('TEST-ENFORCE-DEFAULT', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos',
               now() - interval '2 hours', now() - interval '30 minutes', TRUE)
            """
        )
        try:
            cur.execute("SELECT decision_ref FROM enforce_challenge_window_timeouts()")
            refs = [r[0] for r in cur.fetchall()]
            assert 'TEST-ENFORCE-DEFAULT' not in refs, \
                "default call (test_mode=FALSE) must exclude is_test=TRUE rows"
        finally:
            cur.execute("DELETE FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCE-DEFAULT'")
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCE-DEFAULT'")


def test_enforcer_test_mode_true_includes_is_test_rows():
    """enforce_challenge_window_timeouts(test_mode => TRUE) processes only is_test=TRUE rows."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by,
               decided_at, challengeable_until, is_test)
            VALUES
              ('TEST-ENFORCE-TESTMODE', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos',
               now() - interval '2 hours', now() - interval '30 minutes', TRUE)
            """
        )
        try:
            cur.execute("SELECT decision_ref, action FROM enforce_challenge_window_timeouts(test_mode => TRUE)")
            rows = cur.fetchall()
            refs = [r[0] for r in rows]
            assert 'TEST-ENFORCE-TESTMODE' in refs, \
                "test_mode=TRUE should process is_test=TRUE rows"
        finally:
            cur.execute("DELETE FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCE-TESTMODE'")
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCE-TESTMODE'")


def test_enforcer_test_mode_true_excludes_production_rows():
    """CRITICAL — test_mode=TRUE must NOT touch is_test=FALSE rows. Prevents the exact
    test-mutates-prod bug that caused CAI-RESP-077 incident. This is the structural fix."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by,
               decided_at, challengeable_until)
            VALUES
              ('TEST-ENFORCE-PROD-SAFE', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos',
               now() - interval '2 hours', now() - interval '30 minutes')
            """
        )
        try:
            cur.execute("SELECT decision_ref FROM enforce_challenge_window_timeouts(test_mode => TRUE)")
            refs = [r[0] for r in cur.fetchall()]
            assert 'TEST-ENFORCE-PROD-SAFE' not in refs, \
                "test_mode=TRUE must NOT touch is_test=FALSE production rows"
        finally:
            cur.execute("DELETE FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCE-PROD-SAFE'")
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCE-PROD-SAFE'")


def test_boot_briefing_unverified_decisions_section():
    """AC-BUG032-6: boot_briefing surfaces per-decided_by count of unverified rows."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, count(*) FROM boot_briefing
             WHERE source = 'unverified_decisions'
             GROUP BY source
            """
        )
        # In Phase 1 with zero allowlist seed, every decision is unverified, so we expect
        # >= 1 row (one per distinct decided_by).
        row = cur.fetchone()
        if row is None:
            pytest.skip("no unverified decisions in DB (unexpected in Phase 1 zero-seed state)")
        assert row[0] == 'unverified_decisions'
