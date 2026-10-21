"""TDD proof for migrations/114_work_item_ssot.sql (bus #73169/#73176/#73179).

Two layers:
  1. A dry-run through the canonical scripts/apply_migration.py -- proves the file is
     well-formed for the tool every real apply must go through (ledger header, no
     forbidden top-level transaction control, no unasserted REVOKE/DROP).
  2. Direct behavioral exercise of the DDL (triggers, derive functions, DROP-INVARIANT
     CHECKs, the no-op audit guard, backfill idempotency) against the ephemeral PG17
     harness -- applying the raw file content directly (bypassing apply_migration's
     production-silo --gate requirement, which is out of scope for a local proof; the
     gate mechanism itself is apply_migration.py's own, separately-tested contract).

Runs entirely against tests/conftest.py's ephemeral pg_dsn. NEVER touches DATABASE_URL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

MIGRATION_PATH = Path(__file__).resolve().parent.parent.parent / "migrations" / "114_work_item_ssot.sql"
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
    """Minimal stand-ins for the 3 real source tables -- only the columns the
    migration's triggers/derive functions actually touch, matching the live
    substrate's column names and defaults exactly (checked live against
    tscuymavysscrvoberrr before writing this)."""
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


@pytest.fixture
def wi_db(fresh_db):
    """Source tables created, migration applied directly (raw execute, bypassing
    apply_migration's --gate) against a clean ephemeral schema."""
    dsn = f"{fresh_db} application_name={SILO}"
    _make_source_tables(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_PATH.read_text())
    return dsn


def _q(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _exec(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


# ── Layer 1: apply_migration.py compatibility (dry-run; no --gate needed) ───────────
def test_dry_run_via_apply_migration(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    _make_ledger_table(dsn)
    _make_source_tables(dsn)
    result = am.apply_migration(dsn, MIGRATION_PATH, silo=SILO, dry_run=True)
    assert result["status"] == "dry_run_ok"


# ── ASK ──────────────────────────────────────────────────────────────────────────────
def test_ask_captured_default(wi_db):
    _exec(wi_db, "INSERT INTO operator_asks (ask) VALUES ('ping')")
    rows = _q(wi_db, "SELECT state, owner_agent FROM work_item WHERE source_table='operator_asks'")
    assert rows == [("captured", None)]


def test_ask_triaged_and_delegated_becomes_owned(wi_db):
    _exec(wi_db, "INSERT INTO operator_asks (ask) VALUES ('ping') RETURNING id")
    _exec(wi_db, "UPDATE operator_asks SET triage_state='ask', delegated_to='cc-irsyad-coord'")
    rows = _q(wi_db, "SELECT state, owner_agent FROM work_item WHERE source_table='operator_asks'")
    assert rows == [("owned", "cc-irsyad-coord")]


def test_ask_not_an_ask_becomes_dropped_with_attribution(wi_db):
    _exec(wi_db, "INSERT INTO operator_asks (ask) VALUES ('ping')")
    _exec(wi_db, "UPDATE operator_asks SET triage_state='not_an_ask', triaged_by='orch-console'")
    rows = _q(wi_db, "SELECT state, dropped_by, drop_reason FROM work_item WHERE source_table='operator_asks'")
    assert rows == [("dropped", "orch-console", "not_an_ask (operator_asks triage)")]


def test_ask_done_without_triaged_by_falls_back_to_source_tag(wi_db):
    _exec(wi_db, "INSERT INTO operator_asks (ask) VALUES ('ping')")
    _exec(wi_db, "UPDATE operator_asks SET triage_state='done'")
    rows = _q(wi_db, "SELECT state, done_by FROM work_item WHERE source_table='operator_asks'")
    assert rows == [("done", "source:operator_asks")]


# ── BUS (requires_response subset) ──────────────────────────────────────────────────
def test_bus_non_rr_message_never_mirrored(wi_db):
    _exec(wi_db, "INSERT INTO agent_messages (from_agent, subject, requires_response) "
                 "VALUES ('orch-console', 'fyi', false)")
    rows = _q(wi_db, "SELECT count(*) FROM work_item WHERE source_table='agent_messages'")
    assert rows == [(0,)]


def test_bus_rr_message_lifecycle_captured_owned_done(wi_db):
    _exec(wi_db, "INSERT INTO agent_messages (from_agent, subject, requires_response) "
                 "VALUES ('orch-console', 'need review', true)")
    assert _q(wi_db, "SELECT state FROM work_item WHERE source_table='agent_messages'") == [("captured",)]

    _exec(wi_db, "UPDATE agent_messages SET claimed_by='cc-fleet-health-1'")
    assert _q(wi_db, "SELECT state, owner_agent FROM work_item WHERE source_table='agent_messages'") \
        == [("owned", "cc-fleet-health-1")]

    _exec(wi_db, "INSERT INTO agent_messages (from_agent, subject, requires_response) "
                 "VALUES ('cc-fleet-health-1', 're: need review', false) RETURNING id")
    reply_id = _q(wi_db, "SELECT id FROM agent_messages WHERE subject='re: need review'")[0][0]
    _exec(wi_db, "UPDATE agent_messages SET responded_at=now(), response_ref=%s "
                 "WHERE subject='need review'", (str(reply_id),))
    rows = _q(wi_db, "SELECT state, done_by FROM work_item WHERE source_table='agent_messages' "
                     "AND source_id != %s", (reply_id,))
    assert rows == [("done", "cc-fleet-health-1")]


def test_bus_skipped_becomes_dropped(wi_db):
    _exec(wi_db, "INSERT INTO agent_messages (from_agent, subject, requires_response) "
                 "VALUES ('orch-console', 'noise', true)")
    _exec(wi_db, "UPDATE agent_messages SET skipped_at=now()")
    rows = _q(wi_db, "SELECT state, dropped_by, drop_reason FROM work_item WHERE source_table='agent_messages'")
    assert rows == [("dropped", "source:agent_messages", "skipped (priority_sla_watchdog)")]


# ── COMMITMENT ───────────────────────────────────────────────────────────────────────
def test_commitment_pending_is_owned_from_creation(wi_db):
    _exec(wi_db, "INSERT INTO held_commitments (owner_agent, title, due_at) "
                 "VALUES ('cc-irsyad-coord', 'ship the doc', now())")
    rows = _q(wi_db, "SELECT state, owner_agent FROM work_item WHERE source_table='held_commitments'")
    assert rows == [("owned", "cc-irsyad-coord")]


def test_commitment_pending_blocked_is_in_progress(wi_db):
    _exec(wi_db, "INSERT INTO held_commitments (owner_agent, title, due_at, blocked_on) "
                 "VALUES ('cc-irsyad-coord', 'ship the doc', now(), 'waiting on client')")
    rows = _q(wi_db, "SELECT state FROM work_item WHERE source_table='held_commitments'")
    assert rows == [("in_progress",)]


def test_commitment_fired_is_in_progress(wi_db):
    _exec(wi_db, "INSERT INTO held_commitments (owner_agent, title, due_at) "
                 "VALUES ('cc-irsyad-coord', 'ship the doc', now())")
    _exec(wi_db, "UPDATE held_commitments SET status='fired'")
    rows = _q(wi_db, "SELECT state FROM work_item WHERE source_table='held_commitments'")
    assert rows == [("in_progress",)]


def test_commitment_discharged_is_done_with_real_attribution(wi_db):
    _exec(wi_db, "INSERT INTO held_commitments (owner_agent, title, due_at) "
                 "VALUES ('cc-irsyad-coord', 'ship the doc', now())")
    _exec(wi_db, "UPDATE held_commitments SET status='discharged', discharged_at=now(), "
                 "discharged_by='cc-irsyad-coord'")
    rows = _q(wi_db, "SELECT state, done_by FROM work_item WHERE source_table='held_commitments'")
    assert rows == [("done", "cc-irsyad-coord")]


def test_commitment_cancelled_is_dropped_with_fallback_attribution(wi_db):
    _exec(wi_db, "INSERT INTO held_commitments (owner_agent, title, due_at) "
                 "VALUES ('cc-irsyad-coord', 'ship the doc', now())")
    _exec(wi_db, "UPDATE held_commitments SET status='cancelled', cancelled_at=now(), "
                 "cancel_reason='client dropped the ask'")
    rows = _q(wi_db, "SELECT state, dropped_by, drop_reason FROM work_item WHERE source_table='held_commitments'")
    assert rows == [("dropped", "source:held_commitments", "client dropped the ask")]


# ── DROP-INVARIANT CHECK constraints (defense in depth -- the derive functions already
# guarantee this, but a direct write must be refused too, same as held_commitments). ──
def test_direct_done_without_attribution_refused(wi_db):
    with pytest.raises(psycopg.errors.CheckViolation):
        _exec(wi_db, "INSERT INTO work_item (kind, state, title, source_table, source_id) "
                     "VALUES ('ask', 'done', 'x', 'operator_asks', 999999)")


def test_direct_owned_without_owner_refused(wi_db):
    with pytest.raises(psycopg.errors.CheckViolation):
        _exec(wi_db, "INSERT INTO work_item (kind, state, title, source_table, source_id) "
                     "VALUES ('ask', 'owned', 'x', 'operator_asks', 999999)")


# ── Audit: only on an ACTUAL state change ───────────────────────────────────────────
def test_audit_written_once_on_insert_and_once_on_real_transition_not_on_noop_update(wi_db):
    _exec(wi_db, "INSERT INTO operator_asks (ask) VALUES ('ping')")
    item_id = _q(wi_db, "SELECT id FROM work_item WHERE source_table='operator_asks'")[0][0]
    assert _q(wi_db, "SELECT count(*) FROM work_item_audit WHERE work_item_id=%s", (item_id,)) == [(1,)]

    # No-op update: same triage_state ('captured'), only an unrelated-in-practice column
    # re-set to its current value. Must NOT add a second audit row.
    _exec(wi_db, "UPDATE operator_asks SET ask='ping'")
    assert _q(wi_db, "SELECT count(*) FROM work_item_audit WHERE work_item_id=%s", (item_id,)) == [(1,)]

    # Real transition: captured -> owned. Must add exactly one more row.
    _exec(wi_db, "UPDATE operator_asks SET triage_state='ask', delegated_to='cc-x'")
    rows = _q(wi_db, "SELECT event, note FROM work_item_audit WHERE work_item_id=%s ORDER BY at", (item_id,))
    assert [r[0] for r in rows] == ["mirrored", "state_changed"]
    assert rows[1][1] == "captured -> owned"


# ── Backfill: idempotent / safely re-runnable ───────────────────────────────────────
_BACKFILL_SQL = """
SELECT work_item_mirror('ask', 'operator_asks', oa.id,
         work_item_derive_ask(oa.id, oa.ask, oa.triage_state, oa.closed_at,
                               oa.delegated_to, oa.committed_date, oa.triaged_by),
         'backfill:test')
FROM operator_asks oa WHERE oa.closed_at IS NULL;
"""


def test_backfill_rerun_is_idempotent_no_duplicate_rows_no_duplicate_audit(wi_db):
    # Insert directly (no migration's own INSERT trigger side effect skipped -- the
    # trigger already mirrors it on INSERT; re-running the backfill SQL afterward must
    # be a true no-op, exactly the scenario of re-running tomorrow's backfill step).
    _exec(wi_db, "INSERT INTO operator_asks (ask) VALUES ('open ask')")
    item_id = _q(wi_db, "SELECT id FROM work_item WHERE source_table='operator_asks'")[0][0]
    assert _q(wi_db, "SELECT count(*) FROM work_item WHERE source_table='operator_asks'") == [(1,)]
    assert _q(wi_db, "SELECT count(*) FROM work_item_audit WHERE work_item_id=%s", (item_id,)) == [(1,)]

    _exec(wi_db, _BACKFILL_SQL)
    _exec(wi_db, _BACKFILL_SQL)

    assert _q(wi_db, "SELECT count(*) FROM work_item WHERE source_table='operator_asks'") == [(1,)]
    assert _q(wi_db, "SELECT count(*) FROM work_item_audit WHERE work_item_id=%s", (item_id,)) == [(1,)]
