"""TDD proof for migrations/134_work_item_ask_chase_by_due_at.sql (op#35246,
console bus #81038/#81040): the work_item mirror of an operator_asks row must
derive due_at from GREATEST(committed_date, chase_by), not committed_date
alone -- a deliberately-deferred ask (future chase_by, no committed_date yet)
was mirroring with due_at NULL, so the governance_floor 72h-abandoned check
flagged it as stuck.

Two layers, same convention as test_114/test_132:
  1. A dry-run through the canonical scripts/apply_migration.py.
  2. Direct behavioral exercise of the trigger + backfill against a hand-built
     "114 already ran, 134 has not yet" fixture (the ORIGINAL pre-134
     work_item_derive_ask()/work_item_mirror_ask_trg()/trigger), applying 134's
     raw file content directly.

Runs entirely against tests/conftest.py's ephemeral pg_dsn. NEVER touches
DATABASE_URL.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

MIGRATION_PATH = Path(__file__).resolve().parent.parent.parent / "migrations" / "134_work_item_ask_chase_by_due_at.sql"
SILO = "tscuymavysscrvoberrr"


def _future(hours: int) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).isoformat()


def _ensure_role(dsn: str, rolename: str) -> bool:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rolename,))
        if cur.fetchone():
            return False
        cur.execute(f'CREATE ROLE "{rolename}" NOLOGIN')
        return True


def _drop_roles_created(dsn: str, created: list[str]) -> None:
    if not created:
        return
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for rolename in created:
            cur.execute(f'DROP ROLE IF EXISTS "{rolename}"')


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


def _make_pre_134_source(dsn: str) -> None:
    """Minimal stand-in for the relevant subset of migration 114: operator_asks,
    work_item/work_item_audit, work_item_derived, work_item_mirror(), and the
    ORIGINAL (pre-134) work_item_derive_ask()/work_item_mirror_ask_trg() + trigger."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE operator_asks (
                 id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 ask             text NOT NULL,
                 triage_state    text NOT NULL DEFAULT 'captured',
                 closed_at       timestamptz,
                 delegated_to    text,
                 committed_date  timestamptz,
                 chase_by        timestamptz,
                 triaged_by      text,
                 created_at      timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            """CREATE TABLE work_item (
                 id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 kind          text NOT NULL,
                 state         text NOT NULL,
                 owner_agent   text,
                 title         text,
                 source_table  text NOT NULL,
                 source_id     bigint NOT NULL,
                 source_ref    text,
                 due_at        timestamptz,
                 done_at       timestamptz,
                 done_by       text,
                 dropped_at    timestamptz,
                 dropped_by    text,
                 drop_reason   text,
                 created_at    timestamptz NOT NULL DEFAULT now(),
                 updated_at    timestamptz NOT NULL DEFAULT now(),
                 UNIQUE (source_table, source_id)
               )"""
        )
        cur.execute(
            """CREATE TABLE work_item_audit (
                 id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 work_item_id  bigint NOT NULL,
                 event         text NOT NULL, actor text NOT NULL,
                 at            timestamptz NOT NULL DEFAULT now(), note text
               )"""
        )
        cur.execute(
            """CREATE TYPE work_item_derived AS (
                 state text, owner_agent text, title text, source_ref text,
                 due_at timestamptz, done_at timestamptz, done_by text,
                 dropped_at timestamptz, dropped_by text, drop_reason text
               )"""
        )
        cur.execute(
            """CREATE OR REPLACE FUNCTION work_item_mirror(
                 p_kind text, p_source_table text, p_source_id bigint,
                 d work_item_derived, p_actor text
               ) RETURNS void LANGUAGE plpgsql AS $$
               DECLARE
                   prior_state text;
                   v_id bigint;
               BEGIN
                   SELECT state INTO prior_state FROM work_item
                    WHERE source_table = p_source_table AND source_id = p_source_id;

                   INSERT INTO work_item (kind, state, owner_agent, title, source_table, source_id,
                                           source_ref, due_at, done_at, done_by, dropped_at,
                                           dropped_by, drop_reason, updated_at)
                   VALUES (p_kind, d.state, d.owner_agent, d.title, p_source_table, p_source_id,
                           d.source_ref, d.due_at, d.done_at, d.done_by, d.dropped_at, d.dropped_by,
                           d.drop_reason, now())
                   ON CONFLICT (source_table, source_id) DO UPDATE SET
                       state = EXCLUDED.state, owner_agent = EXCLUDED.owner_agent,
                       title = EXCLUDED.title, source_ref = EXCLUDED.source_ref,
                       due_at = EXCLUDED.due_at, done_at = EXCLUDED.done_at,
                       done_by = EXCLUDED.done_by, dropped_at = EXCLUDED.dropped_at,
                       dropped_by = EXCLUDED.dropped_by, drop_reason = EXCLUDED.drop_reason,
                       updated_at = now()
                   RETURNING id INTO v_id;

                   IF prior_state IS NULL OR prior_state IS DISTINCT FROM d.state THEN
                       INSERT INTO work_item_audit (work_item_id, event, actor, note)
                       VALUES (v_id, CASE WHEN prior_state IS NULL THEN 'mirrored' ELSE 'state_changed' END,
                               p_actor, COALESCE(prior_state, '(new)') || ' -> ' || d.state);
                   END IF;
               END;
               $$"""
        )
        cur.execute(
            """CREATE OR REPLACE FUNCTION work_item_derive_ask(
                 p_id bigint, p_ask text, p_triage_state text, p_closed_at timestamptz,
                 p_delegated_to text, p_committed_date timestamptz, p_triaged_by text
               ) RETURNS work_item_derived LANGUAGE plpgsql AS $$
               DECLARE
                   d work_item_derived;
               BEGIN
                   d.title      := left(p_ask, 200);
                   d.source_ref := 'operator_asks id=' || p_id;
                   d.due_at     := p_committed_date;

                   IF p_triage_state = 'not_an_ask' THEN
                       d.state := 'dropped';
                       d.dropped_at  := COALESCE(p_closed_at, now());
                       d.dropped_by  := COALESCE(p_triaged_by, 'source:operator_asks');
                       d.drop_reason := 'not_an_ask (operator_asks triage)';
                   ELSIF p_triage_state = 'done' OR p_closed_at IS NOT NULL THEN
                       d.state   := 'done';
                       d.done_at := COALESCE(p_closed_at, now());
                       d.done_by := COALESCE(p_triaged_by, 'source:operator_asks');
                       d.owner_agent := p_delegated_to;
                   ELSIF p_triage_state = 'ask' AND p_delegated_to IS NOT NULL THEN
                       d.state := 'owned';
                       d.owner_agent := p_delegated_to;
                   ELSE
                       d.state := 'captured';
                   END IF;

                   RETURN d;
               END;
               $$"""
        )
        cur.execute(
            """CREATE OR REPLACE FUNCTION work_item_mirror_ask_trg() RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   BEGIN
                       PERFORM work_item_mirror('ask', 'operator_asks', NEW.id,
                           work_item_derive_ask(NEW.id, NEW.ask, NEW.triage_state, NEW.closed_at,
                                                 NEW.delegated_to, NEW.committed_date, NEW.triaged_by),
                           'trigger:operator_asks');
                   EXCEPTION WHEN OTHERS THEN
                       RAISE WARNING 'work_item mirror failed for operator_asks id=%: %', NEW.id, SQLERRM;
                   END;
                   RETURN NEW;
               END;
               $$"""
        )
        cur.execute("DROP TRIGGER IF EXISTS work_item_mirror_ask_trg ON operator_asks")
        cur.execute(
            "CREATE TRIGGER work_item_mirror_ask_trg AFTER INSERT OR UPDATE ON operator_asks "
            "FOR EACH ROW EXECUTE FUNCTION work_item_mirror_ask_trg()"
        )


@pytest.fixture
def pre134_db(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in ("anon", "authenticated", "service_role") if _ensure_role(dsn, r)]
    _make_pre_134_source(dsn)
    yield dsn
    _drop_roles_created(dsn, created)


def _apply_134_raw(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_PATH.read_text())


# ── Layer 1: apply_migration.py compatibility (dry-run) ─────────────────────
def test_dry_run_via_apply_migration(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in ("anon", "authenticated", "service_role") if _ensure_role(dsn, r)]
    try:
        _make_ledger_table(dsn)
        _make_pre_134_source(dsn)
        result = am.apply_migration(dsn, MIGRATION_PATH, silo=SILO, dry_run=True)
        assert result["status"] == "dry_run_ok"
    finally:
        _drop_roles_created(dsn, created)


# ── Layer 2: behavioral exercise ─────────────────────────────────────────────
def test_before_fix_future_chase_by_mirrors_due_at_null(pre134_db):
    # proves the bug exists in the pre-134 fixture, so the fix test below is
    # proving something that actually changes.
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, triage_state, delegated_to, chase_by) "
            "VALUES ('deferred', 'ask', 'cc-someone', %s) RETURNING id",
            (_future(hours=100),),
        )
        oid = cur.fetchone()[0]
        cur.execute("SELECT due_at FROM work_item WHERE source_table='operator_asks' AND source_id=%s", (oid,))
        assert cur.fetchone()[0] is None


def test_after_fix_future_chase_by_mirrors_into_due_at(pre134_db):
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, triage_state, delegated_to, chase_by) "
            "VALUES ('deferred', 'ask', 'cc-someone', %s) RETURNING id",
            (_future(hours=100),),
        )
        oid = cur.fetchone()[0]
    _apply_134_raw(pre134_db)
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        # a fresh UPDATE after the fix must derive due_at from chase_by
        cur.execute("UPDATE operator_asks SET triaged_by = 'x' WHERE id = %s", (oid,))
        cur.execute("SELECT due_at FROM work_item WHERE source_table='operator_asks' AND source_id=%s", (oid,))
        due_at = cur.fetchone()[0]
    assert due_at is not None
    assert due_at > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=50)


def test_both_committed_date_and_chase_by_set_due_at_is_the_greater(pre134_db):
    _apply_134_raw(pre134_db)
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, triage_state, delegated_to, committed_date, chase_by) "
            "VALUES ('both', 'ask', 'cc-someone', %s, %s) RETURNING id",
            (_future(hours=10), _future(hours=200)),
        )
        oid = cur.fetchone()[0]
        cur.execute("SELECT due_at FROM work_item WHERE source_table='operator_asks' AND source_id=%s", (oid,))
        due_at = cur.fetchone()[0]
    assert due_at > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=150)


def test_neither_committed_date_nor_chase_by_due_at_stays_null(pre134_db):
    _apply_134_raw(pre134_db)
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, triage_state, delegated_to) "
            "VALUES ('neither', 'ask', 'cc-someone') RETURNING id"
        )
        oid = cur.fetchone()[0]
        cur.execute("SELECT due_at FROM work_item WHERE source_table='operator_asks' AND source_id=%s", (oid,))
        assert cur.fetchone()[0] is None


def test_backfill_corrects_existing_mirrored_row_without_touching_updated_at(pre134_db):
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, triage_state, delegated_to, chase_by) "
            "VALUES ('already mirrored wrong', 'ask', 'cc-someone', %s) RETURNING id",
            (_future(hours=100),),
        )
        oid = cur.fetchone()[0]
        cur.execute(
            "SELECT id, due_at, updated_at FROM work_item WHERE source_table='operator_asks' AND source_id=%s",
            (oid,),
        )
        wid, due_before, updated_before = cur.fetchone()
        assert due_before is None  # the bug, pre-fix

    _apply_134_raw(pre134_db)

    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT due_at, updated_at FROM work_item WHERE id = %s", (wid,))
        due_after, updated_after = cur.fetchone()
    assert due_after is not None
    assert due_after > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=50)
    assert updated_after == updated_before  # backfill must never touch updated_at


def test_backfill_is_a_noop_for_a_row_already_correct(pre134_db):
    # a genuinely stale row (no chase_by, no committed_date) must be left exactly alone
    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, triage_state, delegated_to) "
            "VALUES ('genuinely stale', 'ask', 'cc-someone') RETURNING id"
        )
        oid = cur.fetchone()[0]
        cur.execute(
            "SELECT id, due_at, updated_at FROM work_item WHERE source_table='operator_asks' AND source_id=%s",
            (oid,),
        )
        wid, due_before, updated_before = cur.fetchone()

    _apply_134_raw(pre134_db)

    with psycopg.connect(pre134_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT due_at, updated_at FROM work_item WHERE id = %s", (wid,))
        due_after, updated_after = cur.fetchone()
    assert due_after == due_before  # still NULL, not touched
    assert updated_after == updated_before  # not touched at all
