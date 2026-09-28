"""NOTIFIER-FIX P1 Fix 2: challenge_window timeout enforcer.

Integration tests against orchestrator Supabase. Require DATABASE_URL or
SUPABASE_DB_URL. Migration must be applied before these tests run.

bus #44531: every test runs inside a single transaction on its own connection
that is ALWAYS rolled back at teardown (the `cur` fixture below), never
autocommit + a manual DELETE cleanup. A DELETE-in-`finally` still commits each
statement immediately (autocommit=True) and only cleans up on a graceful
return -- a hard crash (OOM, SIGKILL, timeout) between the write and the
`finally` leaves it permanently in production. A rolled-back transaction
cannot leak: even a dead client connection makes Postgres discard the
in-flight transaction server-side. The one test that flips the *global*
orchestrator_runtime_config.challenge_enforcer_mode row (write_mode) is the
highest-severity case this protects -- CAI-RESP-077 was exactly this class of
incident, a write that committed and outlived the test that made it."""
import os

import psycopg
import pytest

from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "supabase/migrations/20260424_batch1_structural_integrity.sql"
)


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


@pytest.fixture
def cur():
    """A live cursor whose whole transaction is ALWAYS rolled back at
    teardown -- see module docstring (bus #44531)."""
    conn = psycopg.connect(_dsn())
    try:
        with conn.cursor() as cursor:
            yield cursor
    finally:
        conn.rollback()
        conn.close()


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_challenge_status_check_allows_accepted_by_timeout(cur):
    cur.execute(
        """
        INSERT INTO strategic_decisions
          (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, is_test)
        VALUES
          ('TEST-CHECK-TIMEOUT', 't', 'd', 'r', 'architecture', 'active', 'accepted_by_timeout', 'cc-ihsanos', TRUE)
        RETURNING decision_ref
        """
    )
    assert cur.fetchone()[0] == 'TEST-CHECK-TIMEOUT'


def test_runtime_config_table_has_enforcer_mode_row(cur):
    cur.execute(
        "SELECT value FROM orchestrator_runtime_config WHERE key = 'challenge_enforcer_mode'"
    )
    row = cur.fetchone()
    assert row is not None, "runtime_config missing challenge_enforcer_mode row"
    assert row[0] == 'dry_run', f"enforcer should start in dry_run, got {row[0]}"


def test_runtime_config_value_check_rejects_invalid_mode(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            """
            INSERT INTO orchestrator_runtime_config (key, value)
            VALUES ('challenge_enforcer_mode', 'garbage_mode')
            """
        )


def test_dryrun_log_table_exists(cur):
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


def test_dryrun_log_unique_on_decision_ref(cur):
    cur.execute(
        """
        INSERT INTO challenge_enforcer_dryrun_log
          (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
        VALUES ('TEST-DRYRUN-UNIQUE', 'challenge_window', now(), 'accepted_by_timeout')
        """
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(
            """
            INSERT INTO challenge_enforcer_dryrun_log
              (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
            VALUES ('TEST-DRYRUN-UNIQUE', 'challenge_window', now(), 'accepted_by_timeout')
            """
        )


def test_trigger_rejects_insert_challenge_window_with_null_expiry(cur):
    with pytest.raises(psycopg.errors.RaiseException):
        cur.execute(
            """
            INSERT INTO strategic_decisions
              (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, challengeable_until, is_test)
            VALUES
              ('TEST-TRIG-INSERT', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos', NULL, TRUE)
            """
        )


def test_trigger_rejects_update_challenge_window_with_null_expiry(cur):
    """An UPDATE that sets challenge_status='challenge_window' on a row with NULL
    challengeable_until must be rejected — covers CAI-RESP-074 C4."""
    cur.execute(
        """
        INSERT INTO strategic_decisions
          (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, challengeable_until, is_test)
        VALUES
          ('TEST-TRIG-UPDATE', 't', 'd', 'r', 'architecture', 'active', 'informational', 'cc-ihsanos', NULL, TRUE)
        RETURNING decision_ref
        """
    )
    assert cur.fetchone()[0] == 'TEST-TRIG-UPDATE'
    with pytest.raises(psycopg.errors.RaiseException):
        cur.execute(
            """
            UPDATE strategic_decisions
               SET challenge_status = 'challenge_window'
             WHERE decision_ref = 'TEST-TRIG-UPDATE'
            """
        )


def test_trigger_accepts_challenge_window_with_expiry(cur):
    cur.execute(
        """
        INSERT INTO strategic_decisions
          (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by, challengeable_until, is_test)
        VALUES
          ('TEST-TRIG-OK', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos', now() + interval '24 hours', TRUE)
        RETURNING decision_ref
        """
    )
    assert cur.fetchone()[0] == 'TEST-TRIG-OK'


def test_enforcer_function_exists(cur):
    cur.execute(
        """
        SELECT pg_get_function_result(oid) FROM pg_proc
         WHERE proname = 'enforce_challenge_window_timeouts'
        """
    )
    row = cur.fetchone()
    assert row is not None, "enforce_challenge_window_timeouts function not found"
    assert 'text' in row[0].lower()


def test_enforcer_dry_run_logs_not_flips(cur):
    """In dry_run mode, expired rows go to log, strategic_decisions unchanged."""
    cur.execute(
        "SELECT value FROM orchestrator_runtime_config WHERE key = 'challenge_enforcer_mode'"
    )
    mode = cur.fetchone()[0]
    assert mode == 'dry_run', f"test precondition violated: mode={mode}"

    cur.execute(
        """
        INSERT INTO strategic_decisions
          (decision_ref, title, decision, reasoning, domain, status, challenge_status,
           decided_by, decided_at, challengeable_until, is_test)
        VALUES
          ('TEST-ENFORCER-DRY', 't', 'd', 'r', 'architecture', 'active', 'challenge_window',
           'cc-ihsanos', now() - interval '2 hours', now() - interval '30 minutes', TRUE)
        """
    )
    cur.execute("SELECT decision_ref, action FROM enforce_challenge_window_timeouts(test_mode => TRUE)")
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


def test_enforcer_race_guard_skips_recent_rows(cur):
    """Rows with decided_at within last hour must NOT be flipped even if expired."""
    cur.execute(
        """
        INSERT INTO strategic_decisions
          (decision_ref, title, decision, reasoning, domain, status, challenge_status,
           decided_by, decided_at, challengeable_until, is_test)
        VALUES
          ('TEST-ENFORCER-RACE', 't', 'd', 'r', 'architecture', 'active', 'challenge_window',
           'cc-ihsanos', now() - interval '15 minutes', now() - interval '5 minutes', TRUE)
        """
    )
    cur.execute("SELECT decision_ref FROM enforce_challenge_window_timeouts(test_mode => TRUE)")
    rows = cur.fetchall()
    race_rows = [r for r in rows if r[0] == 'TEST-ENFORCER-RACE']
    assert len(race_rows) == 0, f"race guard failed: test row was processed"


def test_pg_cron_job_registered(cur):
    cur.execute(
        "SELECT jobname, schedule, command FROM cron.job WHERE jobname = 'notifier-fix-enforcer'"
    )
    row = cur.fetchone()
    assert row is not None, "pg_cron job 'notifier-fix-enforcer' not registered"
    assert row[1] == '*/5 * * * *', f"expected 5-min schedule, got {row[1]}"
    assert 'enforce_challenge_window_timeouts' in row[2]


def test_enforcer_test_mode_true_full_write_path(cur):
    """BUG-031 per CAI-RESP-077: full write-path test using test_mode=TRUE + is_test=TRUE fixture.
    ZERO production rows touched regardless of enforcer_mode flag value.
    Replaces the old test_enforcer_write_mode_flips_not_logs which caused the incident.

    The mode flip to write_mode below never reaches production: it lives only in
    this test's transaction, which the `cur` fixture always rolls back (bus #44531)."""
    cur.execute("UPDATE orchestrator_runtime_config SET value = 'write_mode' WHERE key = 'challenge_enforcer_mode'")
    # Insert is_test=TRUE fixture matching the predicate
    cur.execute(
        """
        INSERT INTO strategic_decisions
          (decision_ref, title, decision, reasoning, domain, status, challenge_status, decided_by,
           decided_at, challengeable_until, is_test)
        VALUES
          ('TEST-ENFORCE-TESTMODE-WRITE', 't', 'd', 'r', 'architecture', 'active', 'challenge_window', 'cc-ihsanos',
           now() - interval '2 hours', now() - interval '30 minutes', TRUE)
        """
    )
    # Call enforcer with test_mode=TRUE — only is_test=TRUE rows processed
    cur.execute("SELECT decision_ref, action FROM enforce_challenge_window_timeouts(test_mode => TRUE)")
    rows = cur.fetchall()
    test_rows = [r for r in rows if r[0] == 'TEST-ENFORCE-TESTMODE-WRITE']
    assert len(test_rows) == 1
    assert test_rows[0][1] == 'flipped', f"expected flipped, got {test_rows[0][1]}"

    # Test fixture flipped in write_mode+test_mode=TRUE+is_test=TRUE
    cur.execute(
        "SELECT challenge_status FROM strategic_decisions WHERE decision_ref = 'TEST-ENFORCE-TESTMODE-WRITE'"
    )
    assert cur.fetchone()[0] == 'accepted_by_timeout'
