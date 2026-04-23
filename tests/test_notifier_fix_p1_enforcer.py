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
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by)
            VALUES
              ('TEST-CHECK-TIMEOUT', 't', 'd', 'r', 'architecture', 'active', 'accepted_by_timeout', 'cc-ihsanos')
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


def test_dryrun_log_table_exists():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type FROM information_schema.columns
             WHERE table_name = 'challenge_enforcer_dryrun_log'
             ORDER BY ordinal_position
            """
        )
        cols = {r[0]: r[1] for r in cur.fetchall()}
        assert "decision_ref" in cols
        assert cols.get("current_challenge_status") == "text"
        assert cols.get("proposed_new_status") == "text"
        assert "challengeable_until" in cols
        assert "logged_at" in cols
        assert cols.get("processed") == "boolean"


def test_dryrun_log_unique_on_decision_ref():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO challenge_enforcer_dryrun_log
              (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
            VALUES ('TEST-DRYRUN-UNIQUE', 'challenge_window', now(), 'accepted_by_timeout')
            """
        )
        try:
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO challenge_enforcer_dryrun_log
                      (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
                    VALUES ('TEST-DRYRUN-UNIQUE', 'challenge_window', now(), 'accepted_by_timeout')
                    """
                )
        finally:
            cur.execute("DELETE FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-DRYRUN-UNIQUE'")


def test_trigger_rejects_insert_challenge_window_with_null_expiry():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, challengeable_until)
                VALUES
                  ('TEST-TRIG-INSERT', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos', NULL)
                """
            )


def test_trigger_rejects_update_challenge_window_with_null_expiry():
    """An UPDATE that sets challenge_status='challenge_window' on a row with NULL
    challengeable_until must be rejected — covers CAI-RESP-074 C4."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, challengeable_until)
            VALUES
              ('TEST-TRIG-UPDATE', 't', 'd', 'r', 'architecture', 'active', 'informational', 'cc-ihsanos', NULL)
            RETURNING decision_ref
            """
        )
        try:
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute(
                    """
                    UPDATE strategic_decisions
                       SET challenge_status = 'challenge_window'
                     WHERE decision_ref = 'TEST-TRIG-UPDATE'
                    """
                )
        finally:
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-TRIG-UPDATE'")


def test_trigger_accepts_challenge_window_with_expiry():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, challengeable_until)
            VALUES
              ('TEST-TRIG-OK', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos', now() + interval '24 hours')
            RETURNING decision_ref
            """
        )
        try:
            assert cur.fetchone()[0] == 'TEST-TRIG-OK'
        finally:
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-TRIG-OK'")


def test_enforcer_function_exists():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_function_result(oid) FROM pg_proc
             WHERE proname = 'enforce_challenge_window_timeouts'
            """
        )
        row = cur.fetchone()
        assert row is not None, "enforce_challenge_window_timeouts function not found"
        assert 'text' in row[0].lower()


def test_enforcer_dry_run_logs_not_flips():
    """In dry_run mode, expired rows go to log, strategic_decisions unchanged."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM orchestrator_runtime_config WHERE key = 'challenge_enforcer_mode'"
        )
        mode = cur.fetchone()[0]
        assert mode == 'dry_run', f"test precondition violated: mode={mode}"

        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status,
               decided_by, decided_at, challengeable_until)
            VALUES
              ('TEST-ENFORCER-DRY', 't', 'd', 'r', 'architecture', 'active', 'challenge_window',
               'cc-ihsanos', now() - interval '2 hours', now() - interval '30 minutes')
            """
        )
        try:
            cur.execute("SELECT decision_ref, action FROM enforce_challenge_window_timeouts()")
            rows = cur.fetchall()
            logged = [r for r in rows if r[0] == 'TEST-ENFORCER-DRY']
            assert len(logged) == 1, f"expected test row logged, got {rows}"
            assert logged[0][1] == 'logged'

            cur.execute(
                "SELECT challenge_status FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCER-DRY'"
            )
            assert cur.fetchone()[0] == 'challenge_window'

            cur.execute(
                "SELECT proposed_new_status FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCER-DRY'"
            )
            log_row = cur.fetchone()
            assert log_row is not None
            assert log_row[0] == 'accepted_by_timeout'
        finally:
            cur.execute("DELETE FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCER-DRY'")
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCER-DRY'")


def test_enforcer_write_mode_flips_not_logs():
    """In write_mode, expired rows flip directly, dryrun_log not touched."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("UPDATE orchestrator_runtime_config SET value = 'write_mode' WHERE key = 'challenge_enforcer_mode'")
        try:
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status, challenge_status,
                   decided_by, decided_at, challengeable_until)
                VALUES
                  ('TEST-ENFORCER-WRITE', 't', 'd', 'r', 'architecture', 'active', 'challenge_window',
                   'cc-ihsanos', now() - interval '2 hours', now() - interval '30 minutes')
                """
            )
            try:
                cur.execute("SELECT decision_ref, action FROM enforce_challenge_window_timeouts()")
                rows = cur.fetchall()
                flipped = [r for r in rows if r[0] == 'TEST-ENFORCER-WRITE']
                assert len(flipped) == 1
                assert flipped[0][1] == 'flipped'

                cur.execute(
                    "SELECT challenge_status FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCER-WRITE'"
                )
                assert cur.fetchone()[0] == 'accepted_by_timeout'

                cur.execute(
                    "SELECT count(*) FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCER-WRITE'"
                )
                assert cur.fetchone()[0] == 0
            finally:
                cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCER-WRITE'")
        finally:
            cur.execute("UPDATE orchestrator_runtime_config SET value = 'dry_run' WHERE key = 'challenge_enforcer_mode'")


def test_enforcer_race_guard_skips_recent_rows():
    """Rows with decided_at within last hour must NOT be flipped even if expired."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status,
               decided_by, decided_at, challengeable_until)
            VALUES
              ('TEST-ENFORCER-RACE', 't', 'd', 'r', 'architecture', 'active', 'challenge_window',
               'cc-ihsanos', now() - interval '15 minutes', now() - interval '5 minutes')
            """
        )
        try:
            cur.execute("SELECT decision_ref FROM enforce_challenge_window_timeouts()")
            rows = cur.fetchall()
            race_rows = [r for r in rows if r[0] == 'TEST-ENFORCER-RACE']
            assert len(race_rows) == 0, f"race guard failed: test row was processed"
        finally:
            cur.execute("DELETE FROM challenge_enforcer_dryrun_log WHERE decision_ref = 'TEST-ENFORCER-RACE'")
            cur.execute("DELETE FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCER-RACE'")


def test_pg_cron_job_registered():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT jobname, schedule, command FROM cron.job WHERE jobname = 'notifier-fix-enforcer'"
        )
        row = cur.fetchone()
        assert row is not None, "pg_cron job 'notifier-fix-enforcer' not registered"
        assert row[1] == '*/5 * * * *', f"expected 5-min schedule, got {row[1]}"
        assert 'enforce_challenge_window_timeouts' in row[2]
