"""TDD proof for migrations/140_operator_messages_media_status_unrecoverable.sql
and for nervous_system/ingest.py's schema-drift dead-man's switch that guards it.

held_commitment #964 (source bus #85768). Migration 115 constrained
operator_messages.media_status to (pending, ok, failed), and media_reaper
retries every 'pending'/'failed' row forever. But some getFile answers are
permanent — 400 "file is too big" (Bot API cannot download >20 MB), 400
"invalid file_id"/expired file reference, 404 — so those rows re-entered the
retry loop and re-paged the console every 2h for something no retry can fix.
140 widens the CHECK with a TERMINAL value, 'unrecoverable'.

Runs against tests/conftest.py's ephemeral pg_dsn ONLY. Never DATABASE_URL.

The second half is the important half. Widening a CHECK is safe; WRITING a
value the CHECK does not know is not. If this code deploys ahead of the
migration, ingest's envelope INSERT would be rejected — which rolls back the
dedup row, leaves the poll offset unadvanced, and makes Telegram redeliver the
same oversized file forever: a poison update that stalls the whole channel.
`_insert_operator_message` therefore downgrades to 'failed' inside a savepoint
and logs LOUD. These tests are WET (real Postgres, real constraint), because a
fake-DB test cannot show that a swallowed exception leaves a usable
transaction behind.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import apply_migration as am  # noqa: E402

from nervous_system import ingest  # noqa: E402

MIGRATION_140 = ROOT / "migrations" / "140_operator_messages_media_status_unrecoverable.sql"
SILO = "tscuymavysscrvoberrr"

_MEDIA_CHECK_115 = ("media_status IS NULL OR media_status IN "
                    "('pending','ok','failed')")
_MEDIA_CHECK_140 = ("media_status IS NULL OR media_status IN "
                    "('pending','ok','failed','unrecoverable')")


def _make_115_era_table(dsn: str, extra_checks: str = "") -> None:
    """The pre-140 schema. Deliberately minimal: 140 touches only the
    media_status CHECK, so the rest of operator_messages is irrelevant here."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""CREATE TABLE operator_messages (
                  id bigserial PRIMARY KEY,
                  direction text NOT NULL,
                  channel text,
                  chat_id text,
                  tag text,
                  text text,
                  delivered boolean NOT NULL DEFAULT true,
                  from_user_id bigint,
                  from_username text,
                  from_name text,
                  cos_triage text,
                  file_id text,
                  file_unique_id text,
                  media_kind text,
                  media_status text,
                  CONSTRAINT operator_messages_media_status_check
                    CHECK ({_MEDIA_CHECK_115}){extra_checks}
                )""")


def _make_ledger_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS migration_ledger (
                 repo text NOT NULL,
                 migration_name text NOT NULL,
                 silo_ref text NOT NULL,
                 sha256 text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text,
                 note text,
                 PRIMARY KEY (repo, migration_name, silo_ref)
               )""")


@pytest.fixture
def pre_140_db(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    _make_115_era_table(dsn)
    _make_ledger_table(dsn)
    return dsn


@pytest.fixture
def post_140_db(pre_140_db):
    with psycopg.connect(pre_140_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_140.read_text())
    return pre_140_db


def _q(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description:
            return cur.fetchall()
    return None


def _constraint_def(dsn: str) -> str:
    rows = _q(dsn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                   "WHERE conname = 'operator_messages_media_status_check'")
    assert rows, "the media_status CHECK must still exist after 140"
    return rows[0][0]


# ── the migration itself ────────────────────────────────────────────────────

def test_140_widens_the_check_to_accept_unrecoverable(post_140_db):
    definition = _constraint_def(post_140_db)
    assert "unrecoverable" in definition
    for still_valid in ("pending", "ok", "failed"):
        assert still_valid in definition, f"140 must not drop {still_valid}"


def test_every_115_value_still_inserts(post_140_db):
    """Widening must never narrow: a row written by today's code path has to
    keep working across the deploy."""
    for status in (None, "pending", "ok", "failed", "unrecoverable"):
        _q(post_140_db,
           "INSERT INTO operator_messages (direction, media_status) "
           "VALUES ('inbound', %s)", (status,))
    assert _q(post_140_db, "SELECT count(*) FROM operator_messages")[0][0] == 5


def test_an_unknown_status_is_still_rejected(post_140_db):
    """The constraint must stay a real constraint — widening is not removal."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _q(post_140_db,
           "INSERT INTO operator_messages (direction, media_status) "
           "VALUES ('inbound', 'totally-made-up')")


def test_140_is_idempotent(post_140_db):
    """Re-running a migration must be a no-op, not an error (apply_migration
    may retry after a partial-silo rollout)."""
    with psycopg.connect(post_140_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_140.read_text())
        cur.execute(MIGRATION_140.read_text())
    assert "unrecoverable" in _constraint_def(post_140_db)


def test_dry_run_via_apply_migration(pre_140_db):
    """140 must be acceptable to the real gate (sha256 recorded, silo resolved)
    before anyone runs it for real."""
    result = am.apply_migration(pre_140_db, MIGRATION_140, silo=SILO, dry_run=True)
    assert result["sha256"]


# ── ingest's dead-man's switch (wet: real constraint, real transaction) ─────

def _insert_via_ingest(dsn, media_status="unrecoverable", tag="operator-orch"):
    """Call the REAL helper on a REAL connection, exactly as process_update
    does: inside an already-open transaction, followed by a commit."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO operator_messages (direction, tag) "
                        "VALUES ('inbound', 'sentinel-before')")
            op_id, stored = ingest._insert_operator_message(
                conn, cur, chat_id="123", tag=tag, content="sent a FILE",
                from_user_id=1, from_username="op", from_name="Op",
                cos_triage=None, file_id="F1", file_unique_id="U1",
                media_kind="document", media_status=media_status)
            cur.execute("INSERT INTO operator_messages (direction, tag) "
                        "VALUES ('inbound', 'sentinel-after')")
        conn.commit()
    return op_id, stored


def test_unrecoverable_is_stored_when_the_migration_is_present(post_140_db):
    op_id, stored = _insert_via_ingest(post_140_db)
    assert stored == "unrecoverable"
    row = _q(post_140_db, "SELECT media_status FROM operator_messages WHERE id=%s",
             (op_id,))[0][0]
    assert row == "unrecoverable"


def test_code_ahead_of_migration_downgrades_instead_of_losing_the_message(pre_140_db):
    """THE deploy-ordering proof. With the 115-era CHECK still in place, the
    helper must store 'failed' and keep going — not raise, and not leave the
    outer transaction poisoned (both sentinels must commit)."""
    op_id, stored = _insert_via_ingest(pre_140_db)
    assert stored == "failed"
    row = _q(pre_140_db, "SELECT media_status FROM operator_messages WHERE id=%s",
             (op_id,))[0][0]
    assert row == "failed"
    tags = {r[0] for r in _q(pre_140_db, "SELECT tag FROM operator_messages")}
    assert tags == {"sentinel-before", "sentinel-after", "operator-orch"}, \
        "the savepoint rollback must not have taken the surrounding writes with it"


def test_a_different_check_violation_is_still_raised(fresh_db):
    """Only OUR constraint may be downgraded. Any other rejection is real
    schema drift and must fail loud — silently guessing is how a genuine data
    loss gets hidden."""
    dsn = f"{fresh_db} application_name={SILO}"
    _make_115_era_table(
        dsn, extra_checks=",\n  CONSTRAINT no_poison_tag CHECK (tag <> 'poison')")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_via_ingest(dsn, tag="poison")


def test_a_non_media_row_still_inserts_normally(post_140_db):
    op_id, stored = _insert_via_ingest(post_140_db, media_status=None)
    assert stored is None
