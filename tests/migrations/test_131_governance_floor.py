"""TDD proof for migrations/131_governance_floor.sql (op#35246 Phase 1a/1b,
PR#555 reports/joint-plan-20261028/PLAN.md section 3).

Phase 1a: governance_floor + governance_floor_audit, append-only by GRANT
(CAI-1018/1019 doctrine: REVOKE ALL from anon/authenticated/service_role, GRANT
BACK exactly the intended set), op_ref NOT NULL at the DB layer.

Phase 1b: closes 3 live gaps found on work_item/work_item_audit (migration 114):
  (1) no AFTER DELETE trigger -> a deleted source row orphaned its mirror
  (2) no re-sync when agent_messages.requires_response flips true->false
  (3) no RLS + console_readonly had ZERO grants (live-verified via
      information_schema.role_table_grants before writing this file -- see bus
      #80523) -- work_item/work_item_audit currently carry the SAME Supabase
      default-privilege leak already fixed elsewhere (migration 039, 049):
      authenticated held DELETE/INSERT/UPDATE, service_role held
      DELETE/UPDATE/TRUNCATE, on BOTH tables, live.

Runs entirely against tests/conftest.py's ephemeral pg_dsn. NEVER touches
DATABASE_URL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

MIGRATION_114 = Path(__file__).resolve().parent.parent.parent / "migrations" / "114_work_item_ssot.sql"
MIGRATION_131 = Path(__file__).resolve().parent.parent.parent / "migrations" / "131_governance_floor.sql"
SILO = "tscuymavysscrvoberrr"


def _make_ledger_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE migration_ledger (
                 repo text NOT NULL,
                 migration_name text NOT NULL,
                 silo_ref text NOT NULL,
                 sha256 text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text,
                 note text,
                 PRIMARY KEY (repo, migration_name, silo_ref)
               )"""
        )


def _make_source_tables(dsn: str) -> None:
    """Same minimal stand-ins as test_114_work_item_ssot.py (kept in sync with
    the live substrate's column names/defaults, per that file's own note)."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE operator_asks (
                id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                ask             text NOT NULL,
                triage_state    text NOT NULL DEFAULT 'captured'
                                CHECK (triage_state IN ('captured','ask','not_an_ask','done')),
                closed_at       timestamptz,
                delegated_to    text,
                committed_date  timestamptz,
                triaged_by      text
            )
        """)
        cur.execute("""
            CREATE TABLE agent_messages (
                id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                from_agent         text NOT NULL,
                to_agent           text,
                subject            text NOT NULL,
                requires_response  boolean NOT NULL DEFAULT false,
                responded_at       timestamptz,
                skipped_at         timestamptz,
                claimed_by         text,
                response_ref       text,
                created_at         timestamptz NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE held_commitments (
                id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                owner_agent     text NOT NULL,
                title           text NOT NULL,
                status          text NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','fired','discharged','cancelled')),
                due_at          timestamptz,
                blocked_on      text,
                discharged_at   timestamptz,
                discharged_by   text,
                cancelled_at    timestamptz,
                cancel_reason   text
            )
        """)


def _ensure_role(dsn: str, rolename: str) -> bool:
    """Roles are cluster-wide in the shared session-scoped pg_dsn harness, so a
    second test creating the same role must not error (same helper shape as
    test_ddl_coverage_watchdog.py's _ensure_role). Returns True iff THIS call
    created the role (it didn't already exist) -- the caller must drop any
    role it created, or it leaks into later tests sharing the same cluster.
    Found live: test_durable_inbox_enforcement_123.py's live_applier fixture
    assumes IT creates bus_writer fresh (gaining automatic ADMIN on it); a
    pre-existing bus_writer left behind by an earlier test makes migration
    123's own `IF NOT EXISTS` guard skip creation, so live_applier never gets
    admin on it -- "permission denied to grant role bus_writer" downstream."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rolename,))
        if cur.fetchone():
            return False
        cur.execute(f'CREATE ROLE "{rolename}" NOLOGIN')
        return True


def _drop_roles_created(dsn: str, created: list[str]) -> None:
    """Teardown counterpart to _ensure_role: reset the schema first (so no
    policy/grant still references the role -- DROP ROLE fails otherwise),
    then drop only the roles THIS test actually created."""
    if not created:
        return
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        for rolename in created:
            cur.execute(f'DROP ROLE IF EXISTS "{rolename}"')


def _q(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _exec(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


@pytest.fixture
def gf_db(fresh_db):
    """Roles present, 114 applied (work_item/work_item_audit exist), then 131
    applied on top -- the real stacking order this ships in. Drops any role
    this fixture created once the test using it is done (see _drop_roles_created)."""
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in ("anon", "authenticated", "service_role", "bus_writer", "console_readonly")
               if _ensure_role(dsn, r)]
    _make_source_tables(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_114.read_text())
        cur.execute(MIGRATION_131.read_text())
    yield dsn
    _drop_roles_created(dsn, created)


# ── Layer 1: apply_migration.py compatibility (dry-run; no --gate needed) ───────────
def test_dry_run_via_apply_migration(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in ("anon", "authenticated", "service_role", "bus_writer", "console_readonly")
               if _ensure_role(dsn, r)]
    _make_ledger_table(dsn)
    _make_source_tables(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_114.read_text())
    try:
        result = am.apply_migration(dsn, MIGRATION_131, silo=SILO, dry_run=True)
        assert result["status"] == "dry_run_ok"
    finally:
        _drop_roles_created(dsn, created)


# ── Phase 1a: governance_floor / governance_floor_audit shape ──────────────────────
def test_governance_floor_insert_requires_op_ref(gf_db):
    with pytest.raises(psycopg.errors.NotNullViolation):
        _exec(gf_db, "INSERT INTO governance_floor (sentence, predicate_sql) "
                     "VALUES ('test floor', 'SELECT true')")


def test_governance_floor_accepts_decision_ref_nullable(gf_db):
    _exec(gf_db, "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
                 "VALUES ('test floor', 'op#1', 'SELECT true')")
    rows = _q(gf_db, "SELECT decision_ref FROM governance_floor WHERE sentence='test floor'")
    assert rows == [(None,)]


def test_governance_floor_audit_insert_requires_op_ref(gf_db):
    _exec(gf_db, "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
                 "VALUES ('test floor', 'op#1', 'SELECT true')")
    floor_id = _q(gf_db, "SELECT id FROM governance_floor WHERE sentence='test floor'")[0][0]
    with pytest.raises(psycopg.errors.NotNullViolation):
        _exec(gf_db, "INSERT INTO governance_floor_audit (floor_id, event, actor) "
                     "VALUES (%s, 'created', 'cc-paperclip-spike-1')", (floor_id,))


def test_governance_floor_audit_rejects_update_and_delete_from_web_roles(gf_db):
    """CAI-1018/1019: REVOKE ALL + GRANT BACK must leave anon/authenticated/
    service_role with zero UPDATE/DELETE -- the exact planted-bad-write shape
    the writer-gate's own tests use, proven via has_table_privilege (the
    EFFECTIVE-grant method, not source inspection)."""
    for role in ("anon", "authenticated", "service_role"):
        for priv in ("UPDATE", "DELETE", "TRUNCATE"):
            rows = _q(gf_db, "SELECT has_table_privilege(%s, 'governance_floor_audit', %s)", (role, priv))
            assert rows == [(False,)], f"{role} still has {priv} on governance_floor_audit"


# ── Phase 1b gap 3 (live-verified, bus #80523): close the SAME leak on work_item ───
def test_work_item_and_audit_reject_write_from_web_roles(gf_db):
    for table in ("work_item", "work_item_audit"):
        for role in ("anon", "authenticated", "service_role"):
            for priv in ("UPDATE", "DELETE", "TRUNCATE"):
                rows = _q(gf_db, "SELECT has_table_privilege(%s, %s, %s)", (role, table, priv))
                assert rows == [(False,)], f"{role} still has {priv} on {table}"


def test_console_readonly_can_now_read_work_item(gf_db):
    """Closes the other half of gap 3: console_readonly had ZERO grants before
    this migration (live-verified, bus #80523)."""
    for table in ("work_item", "work_item_audit", "governance_floor", "governance_floor_audit"):
        rows = _q(gf_db, "SELECT has_table_privilege('console_readonly', %s, 'SELECT')", (table,))
        assert rows == [(True,)], f"console_readonly cannot SELECT {table}"


def test_rls_enabled_on_work_item_and_audit(gf_db):
    rows = _q(gf_db, "SELECT relname, relrowsecurity FROM pg_class "
                     "WHERE relname IN ('work_item', 'work_item_audit') ORDER BY relname")
    assert rows == [("work_item", True), ("work_item_audit", True)]


# ── Phase 1b gap 1: AFTER DELETE doesn't orphan the mirror ─────────────────────────
def test_delete_source_ask_marks_mirror_dropped(gf_db):
    _exec(gf_db, "INSERT INTO operator_asks (ask) VALUES ('ping')")
    ask_id = _q(gf_db, "SELECT id FROM operator_asks")[0][0]
    assert _q(gf_db, "SELECT state FROM work_item WHERE source_table='operator_asks'") == [("captured",)]

    _exec(gf_db, "DELETE FROM operator_asks WHERE id=%s", (ask_id,))

    rows = _q(gf_db, "SELECT state, dropped_by, drop_reason FROM work_item "
                     "WHERE source_table='operator_asks' AND source_id=%s", (ask_id,))
    assert rows == [("dropped", "source:deleted", "source row deleted")]


def test_delete_source_does_not_retcon_an_already_done_item(gf_db):
    """A done commitment whose source row is later archived/deleted must stay
    done -- source deletion must not downgrade a terminal mirror to dropped."""
    _exec(gf_db, "INSERT INTO held_commitments (owner_agent, title, due_at) "
                 "VALUES ('cc-irsyad-coord', 'ship the doc', now())")
    c_id = _q(gf_db, "SELECT id FROM held_commitments")[0][0]
    _exec(gf_db, "UPDATE held_commitments SET status='discharged', discharged_at=now(), "
                 "discharged_by='cc-irsyad-coord' WHERE id=%s", (c_id,))
    assert _q(gf_db, "SELECT state FROM work_item WHERE source_table='held_commitments'") == [("done",)]

    _exec(gf_db, "DELETE FROM held_commitments WHERE id=%s", (c_id,))

    rows = _q(gf_db, "SELECT state, dropped_by FROM work_item "
                     "WHERE source_table='held_commitments' AND source_id=%s", (c_id,))
    assert rows == [("done", None)]


# ── Phase 1b gap 2: requires_response true->false re-syncs the mirror ─────────────
def test_requires_response_cleared_drops_the_stale_mirror(gf_db):
    _exec(gf_db, "INSERT INTO agent_messages (from_agent, subject, requires_response) "
                 "VALUES ('orch-console', 'maybe an ask', true)")
    msg_id = _q(gf_db, "SELECT id FROM agent_messages")[0][0]
    assert _q(gf_db, "SELECT state FROM work_item WHERE source_table='agent_messages'") == [("captured",)]

    _exec(gf_db, "UPDATE agent_messages SET requires_response=false WHERE id=%s", (msg_id,))

    rows = _q(gf_db, "SELECT state, dropped_by, drop_reason FROM work_item "
                     "WHERE source_table='agent_messages' AND source_id=%s", (msg_id,))
    assert rows == [("dropped", "source:agent_messages", "requires_response cleared")]


# ── Floor row #1: the 72h-ABANDONED check ships seeded, as real SQL ───────────
def test_floor_row_1_seeded_and_passes_on_empty_table(gf_db):
    rows = _q(gf_db, "SELECT op_ref, enforced_in_code FROM governance_floor "
                     "WHERE sentence LIKE 'No work_item in owned/in_progress%'")
    assert rows == [("op#32730", True)]

    predicate_sql = _q(gf_db, "SELECT predicate_sql FROM governance_floor "
                              "WHERE sentence LIKE 'No work_item in owned/in_progress%'")[0][0]
    assert _q(gf_db, f"SELECT ({predicate_sql})") == [(True,)]


def test_floor_row_1_predicate_fails_on_a_real_72h_stale_item(gf_db):
    _exec(gf_db, "INSERT INTO operator_asks (ask, triage_state, delegated_to) "
                 "VALUES ('stale ping', 'ask', 'cc-irsyad-coord')")
    _exec(gf_db, "UPDATE work_item SET updated_at = now() - interval '73 hours' "
                 "WHERE source_table='operator_asks'")

    predicate_sql = _q(gf_db, "SELECT predicate_sql FROM governance_floor "
                              "WHERE sentence LIKE 'No work_item in owned/in_progress%'")[0][0]
    assert _q(gf_db, f"SELECT ({predicate_sql})") == [(False,)]


def test_floor_row_1_predicate_passes_when_future_due_at_set(gf_db):
    """The due_at clause is load-bearing: a row parked against a future due
    date is waiting, not abandoned."""
    _exec(gf_db, "INSERT INTO held_commitments (owner_agent, title, due_at) "
                 "VALUES ('cc-irsyad-coord', 'future thing', now() + interval '10 days')")
    _exec(gf_db, "UPDATE work_item SET updated_at = now() - interval '73 hours' "
                 "WHERE source_table='held_commitments'")

    predicate_sql = _q(gf_db, "SELECT predicate_sql FROM governance_floor "
                              "WHERE sentence LIKE 'No work_item in owned/in_progress%'")[0][0]
    assert _q(gf_db, f"SELECT ({predicate_sql})") == [(True,)]


def test_requires_response_cleared_after_done_does_not_retcon(gf_db):
    _exec(gf_db, "INSERT INTO agent_messages (from_agent, subject, requires_response) "
                 "VALUES ('orch-console', 'need review', true)")
    msg_id = _q(gf_db, "SELECT id FROM agent_messages WHERE subject='need review'")[0][0]
    _exec(gf_db, "UPDATE agent_messages SET responded_at=now() WHERE id=%s", (msg_id,))
    assert _q(gf_db, "SELECT state FROM work_item WHERE source_table='agent_messages' "
                     "AND source_id=%s", (msg_id,)) == [("done",)]

    _exec(gf_db, "UPDATE agent_messages SET requires_response=false WHERE id=%s", (msg_id,))

    rows = _q(gf_db, "SELECT state FROM work_item WHERE source_table='agent_messages' "
                     "AND source_id=%s", (msg_id,))
    assert rows == [("done",)]
