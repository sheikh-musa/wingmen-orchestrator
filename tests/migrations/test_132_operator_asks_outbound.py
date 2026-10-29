"""TDD proof for migrations/132_operator_asks_outbound.sql (op#35246, console
bus #80707/#80718/#80719/#80736).

Migration 085's operator_asks_committed_date_needs_outbound_chk CHECK only
proves outbound_msg_id IS NOT NULL -- never that it names a real outbound
send. This migration closes that with a trigger: a committed_date on an OPEN
row (closed_at IS NULL) requires outbound_msg_id to be a real operator_messages
row with direction='outbound'. Clearing both columns to NULL together (how
fleet-health lifts a bad link) is always allowed. A closed historical row is
never touched by the check, even on an unrelated UPDATE.

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

MIGRATION_132 = Path(__file__).resolve().parent.parent.parent / "migrations" / "132_operator_asks_outbound.sql"
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
    """Minimal stand-ins for the two real tables this migration's trigger
    reads/writes -- just the columns the trigger and tests touch."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE operator_messages (
                id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                direction text NOT NULL CHECK (direction IN ('inbound','outbound')),
                channel   text NOT NULL DEFAULT 'telegram',
                tag       text,
                text      text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE operator_asks (
                id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                ask            text NOT NULL,
                created_at     timestamptz NOT NULL DEFAULT now(),
                closed_at      timestamptz,
                outbound_msg_id bigint,
                committed_date  timestamptz,
                CONSTRAINT operator_asks_committed_date_needs_outbound_chk
                    CHECK (committed_date IS NULL OR outbound_msg_id IS NOT NULL)
            )
        """)
        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON operator_asks, operator_messages TO service_role")


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
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        for rolename in created:
            cur.execute(f'DROP ROLE IF EXISTS "{rolename}"')


def _exec(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


def _q(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _insert_outbound(dsn, *, created_at="now()", text="the date is set", tag=None):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO operator_messages (direction, text, created_at, tag) "
            f"VALUES ('outbound', %s, {created_at}, %s) RETURNING id",
            (text, tag),
        )
        return cur.fetchone()[0]


def _insert_inbound(dsn, *, text="hi"):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (direction, text) VALUES ('inbound', %s) RETURNING id",
            (text,),
        )
        return cur.fetchone()[0]


def _insert_ask(dsn, *, created_at="now()", closed_at=None):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO operator_asks (ask, created_at, closed_at) "
            f"VALUES ('test ask', {created_at}, %s) RETURNING id",
            (closed_at,),
        )
        return cur.fetchone()[0]


@pytest.fixture
def oa_db(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in ("anon", "authenticated", "service_role")
               if _ensure_role(dsn, r)]
    _make_source_tables(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_132.read_text())
    yield dsn
    _drop_roles_created(dsn, created)


# ── Layer 1: apply_migration.py compatibility (dry-run) ─────────────────────
def test_dry_run_via_apply_migration(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in ("anon", "authenticated", "service_role")
               if _ensure_role(dsn, r)]
    _make_ledger_table(dsn)
    _make_source_tables(dsn)
    try:
        result = am.apply_migration(dsn, MIGRATION_132, silo=SILO, dry_run=True)
        assert result["status"] == "dry_run_ok"
    finally:
        _drop_roles_created(dsn, created)


# ── Trigger behavior ─────────────────────────────────────────────────────────
def test_bus_row_id_rejected(oa_db):
    """A bus (agent_messages) row id used as outbound_msg_id -- there is no
    such operator_messages row at all -- must be rejected."""
    bogus_bus_id = 80678
    ask_id = _insert_ask(oa_db)
    with pytest.raises(psycopg.errors.RaiseException):
        _exec(oa_db,
              "UPDATE operator_asks SET committed_date = now(), outbound_msg_id = %s WHERE id = %s",
              (bogus_bus_id, ask_id))


def test_inbound_operator_messages_id_rejected(oa_db):
    """A real operator_messages row, but direction='inbound' -- Musa's own
    message cannot have committed anything -- must be rejected."""
    inbound_id = _insert_inbound(oa_db)
    ask_id = _insert_ask(oa_db)
    with pytest.raises(psycopg.errors.RaiseException):
        _exec(oa_db,
              "UPDATE operator_asks SET committed_date = now(), outbound_msg_id = %s WHERE id = %s",
              (inbound_id, ask_id))


def test_real_outbound_id_accepted(oa_db):
    ask_id = _insert_ask(oa_db)
    outbound_id = _insert_outbound(oa_db)
    _exec(oa_db,
          "UPDATE operator_asks SET committed_date = now(), outbound_msg_id = %s WHERE id = %s",
          (outbound_id, ask_id))
    rows = _q(oa_db, "SELECT outbound_msg_id FROM operator_asks WHERE id = %s", (ask_id,))
    assert rows == [(outbound_id,)]


# ── console bus #80764 fix 1: the predates check belongs in the TRIGGER
# itself, not only in asks_triage.py -- the CLI is one writer, the trigger
# covers all of them. ───────────────────────────────────────────────────────
def test_outbound_id_sent_before_the_ask_existed_is_rejected_by_the_trigger(oa_db):
    outbound_id = _insert_outbound(oa_db, created_at="now() - interval '1 day'")
    ask_id = _insert_ask(oa_db)
    with pytest.raises(psycopg.errors.RaiseException):
        _exec(oa_db,
              "UPDATE operator_asks SET committed_date = now(), outbound_msg_id = %s WHERE id = %s",
              (outbound_id, ask_id))


def test_outbound_id_sent_after_the_ask_was_created_is_accepted(oa_db):
    ask_id = _insert_ask(oa_db)
    outbound_id = _insert_outbound(oa_db)
    _exec(oa_db,
          "UPDATE operator_asks SET committed_date = now(), outbound_msg_id = %s WHERE id = %s",
          (outbound_id, ask_id))
    rows = _q(oa_db, "SELECT outbound_msg_id FROM operator_asks WHERE id = %s", (ask_id,))
    assert rows == [(outbound_id,)]


# ── console bus #80764 fix 2 (the "reopen hole"): the trigger only fired on
# UPDATE OF outbound_msg_id, committed_date -- an ask reopened (closed_at set
# back to NULL) while still carrying an old bad link was never checked. ────
def test_reopening_a_closed_row_with_a_bad_link_raises(oa_db):
    with psycopg.connect(oa_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, closed_at, committed_date, outbound_msg_id) "
            "VALUES ('legacy bad link', now(), now(), 80678) RETURNING id"
        )
        ask_id = cur.fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        _exec(oa_db, "UPDATE operator_asks SET closed_at = NULL WHERE id = %s", (ask_id,))


# ── console bus #80804: the reopen-raises case is intended (no reopen
# carrying a fake promise) but the error must say how to fix it. ───────────
def test_reopen_raise_message_names_the_fix_for_a_nonexistent_link(oa_db):
    with psycopg.connect(oa_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, closed_at, committed_date, outbound_msg_id) "
            "VALUES ('legacy bad link', now(), now(), 80678) RETURNING id"
        )
        ask_id = cur.fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException) as excinfo:
        _exec(oa_db, "UPDATE operator_asks SET closed_at = NULL WHERE id = %s", (ask_id,))
    assert "clear committed_date" in str(excinfo.value)
    assert "outbound_msg_id" in str(excinfo.value)


def test_reopen_raise_message_names_the_fix_for_a_predating_link(oa_db):
    outbound_id = _insert_outbound(oa_db, created_at="now() - interval '2 days'")
    with psycopg.connect(oa_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, closed_at, committed_date, outbound_msg_id, created_at) "
            "VALUES ('legacy predating link', now(), now(), %s, now() - interval '1 day') RETURNING id",
            (outbound_id,),
        )
        ask_id = cur.fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException) as excinfo:
        _exec(oa_db, "UPDATE operator_asks SET closed_at = NULL WHERE id = %s", (ask_id,))
    assert "clear committed_date" in str(excinfo.value)
    assert "outbound_msg_id" in str(excinfo.value)


def test_reopening_a_closed_row_with_a_real_link_succeeds(oa_db):
    with psycopg.connect(oa_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO operator_asks (ask) VALUES ('legacy good link') RETURNING id")
        ask_id = cur.fetchone()[0]
    # Sent AFTER the ask was created -- a real, non-predating link.
    outbound_id = _insert_outbound(oa_db)
    _exec(oa_db,
          "UPDATE operator_asks SET closed_at = now(), committed_date = now(), outbound_msg_id = %s "
          "WHERE id = %s",
          (outbound_id, ask_id))
    _exec(oa_db, "UPDATE operator_asks SET closed_at = NULL WHERE id = %s", (ask_id,))
    rows = _q(oa_db, "SELECT closed_at FROM operator_asks WHERE id = %s", (ask_id,))
    assert rows == [(None,)]


def test_closed_historical_row_untouched_by_unrelated_update(oa_db):
    """A closed row with a bogus outbound_msg_id (the shape of the 34 historical
    rows that predate this migration -- closed_at IS NOT NULL from the start,
    so even the INSERT itself is exempt) must not be blocked by an UPDATE that
    doesn't even touch committed_date/outbound_msg_id."""
    with psycopg.connect(oa_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, closed_at, committed_date, outbound_msg_id) "
            "VALUES ('legacy bad link', now(), now(), 80678) RETURNING id"
        )
        ask_id = cur.fetchone()[0]
    # An unrelated column update on the closed row must succeed untouched.
    _exec(oa_db, "UPDATE operator_asks SET ask = 'legacy bad link (edited)' WHERE id = %s", (ask_id,))
    rows = _q(oa_db, "SELECT ask, outbound_msg_id FROM operator_asks WHERE id = %s", (ask_id,))
    assert rows == [("legacy bad link (edited)", 80678)]


def test_clearing_both_columns_together_is_allowed(oa_db):
    """How fleet-health lifts a bad link (console bus #80736): setting
    committed_date AND outbound_msg_id to NULL together on an open row."""
    ask_id = _insert_ask(oa_db)
    outbound_id = _insert_outbound(oa_db)
    _exec(oa_db,
          "UPDATE operator_asks SET committed_date = now(), outbound_msg_id = %s WHERE id = %s",
          (outbound_id, ask_id))
    _exec(oa_db,
          "UPDATE operator_asks SET committed_date = NULL, outbound_msg_id = NULL WHERE id = %s",
          (ask_id,))
    rows = _q(oa_db, "SELECT committed_date, outbound_msg_id FROM operator_asks WHERE id = %s", (ask_id,))
    assert rows == [(None, None)]


def test_a_real_outbound_row_from_the_nazim_console_channel_is_accepted(oa_db):
    """Fleet-health stamps Friday dates from console's own real nazim_send
    message (console bus #80736) -- that exact real write path must work."""
    ask_id = _insert_ask(oa_db)
    outbound_id = _insert_outbound(oa_db, text="Confirmed for Fri 6 Nov.", tag="nazim-console")
    _exec(oa_db,
          "UPDATE operator_asks SET committed_date = '2026-11-06T00:00:00Z', outbound_msg_id = %s WHERE id = %s",
          (outbound_id, ask_id))
    rows = _q(oa_db, "SELECT outbound_msg_id FROM operator_asks WHERE id = %s", (ask_id,))
    assert rows == [(outbound_id,)]


def test_inserting_a_new_row_with_bogus_outbound_id_is_rejected(oa_db):
    with pytest.raises(psycopg.errors.RaiseException):
        _exec(oa_db,
              "INSERT INTO operator_asks (ask, committed_date, outbound_msg_id) "
              "VALUES ('bad from the start', now(), 999999)")


# ── Direct EXECUTE must stay blocked (search_path/no_execute asserts
# themselves are verified by apply_migration.py's dry-run, exercised in
# test_dry_run_via_apply_migration above -- mirrors migration 131's pattern). ─
def test_anon_and_authenticated_cannot_execute_the_trigger_function_directly(oa_db):
    for role in ("anon", "authenticated"):
        rows = _q(oa_db,
                  "SELECT has_function_privilege(%s, 'operator_asks_outbound_msg_trg()', 'EXECUTE')",
                  (role,))
        assert rows == [(False,)], f"{role} can still EXECUTE operator_asks_outbound_msg_trg directly"
