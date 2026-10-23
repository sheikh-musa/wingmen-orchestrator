"""Tests for the durable-inbox reconciler (Tier-1(b) janky-tools audit, op#33149).

classify() is pure and DB-free. find_stuck_messages() needs a real agent_messages
table -- seeded on the shared ephemeral pg_dsn cluster (tests/conftest.py), same
idiom as operator_ledger_db/reaper_db. NEVER the live substrate (op#22741).
"""
import datetime as dt
import pathlib
import sys
import uuid

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib"))
import durable_inbox_reconciler as reconciler  # noqa: E402

NOW = dt.datetime(2026, 10, 24, 12, 0, 0, tzinfo=dt.timezone.utc)

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def durable_inbox_db(pg_dsn):
    """Ephemeral Postgres carrying migration 117's schema (additive only --
    the revoke in 118 is a separate, later, not-yet-applicable step, see its
    header). Schema is dropped and recreated per-test on the shared
    session-scoped cluster, same idiom as operator_ledger_db. pg_dsn is itself
    a freshly-created ephemeral socket-only cluster (tests/conftest.py), so it
    cannot resolve to the real substrate by construction."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        for role in ("anon", "authenticated", "service_role"):
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if cur.fetchone() is None:
                # Fixed literals from the tuple above, not external input --
                # CREATE ROLE can't take a bind parameter for the role name.
                cur.execute(f"CREATE ROLE {role}")
        cur.execute("""
            CREATE TABLE agent_messages (
                id                 BIGSERIAL PRIMARY KEY,
                from_agent         TEXT NOT NULL,
                to_agent           TEXT NOT NULL,
                message_type       TEXT NOT NULL,
                subject            TEXT NOT NULL,
                body               TEXT NOT NULL,
                priority           TEXT NOT NULL,
                requires_response  BOOLEAN NOT NULL DEFAULT false,
                thread_id          UUID NOT NULL,
                sub_tag            TEXT,
                read_at            TIMESTAMPTZ,
                responded_at       TIMESTAMPTZ,
                response_ref       TEXT,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
        cur.execute(migration_117)
    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def _post(conn, **overrides):
    args = dict(
        from_agent="cc-paperclip-spike",
        to_agent="orch-console",
        message_type="update",
        subject="test subject",
        body="test body",
        priority="P2",
        requires_response=False,
        thread_id=str(uuid.uuid4()),
        sub_tag=None,
    )
    args.update(overrides)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM post_agent_message(%(from_agent)s, %(to_agent)s, "
            "%(message_type)s, %(subject)s, %(body)s, %(priority)s, "
            "%(requires_response)s, %(thread_id)s, %(sub_tag)s)",
            args,
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]


def test_classify_never_delivered_is_pending():
    assert reconciler.classify(delivered_at=None, acked_at=None, lease_expires_at=None, now=NOW) == "PENDING"


def test_classify_delivered_and_acked_is_ok():
    delivered = NOW - dt.timedelta(seconds=30)
    acked = NOW - dt.timedelta(seconds=10)
    lease = delivered + dt.timedelta(seconds=60)
    assert reconciler.classify(delivered_at=delivered, acked_at=acked, lease_expires_at=lease, now=NOW) == "OK"


def test_classify_delivered_not_acked_lease_not_expired_is_waiting():
    delivered = NOW - dt.timedelta(seconds=10)
    lease = delivered + dt.timedelta(seconds=60)
    assert reconciler.classify(delivered_at=delivered, acked_at=None, lease_expires_at=lease, now=NOW) == "WAITING"


def test_classify_delivered_not_acked_lease_expired_is_stuck():
    delivered = NOW - dt.timedelta(seconds=120)
    lease = delivered + dt.timedelta(seconds=60)
    assert reconciler.classify(delivered_at=delivered, acked_at=None, lease_expires_at=lease, now=NOW) == "STUCK"


def test_classify_acked_after_lease_expiry_is_still_ok():
    delivered = NOW - dt.timedelta(seconds=120)
    acked = NOW - dt.timedelta(seconds=5)
    lease = delivered + dt.timedelta(seconds=60)
    assert reconciler.classify(delivered_at=delivered, acked_at=acked, lease_expires_at=lease, now=NOW) == "OK"


def test_find_stuck_messages_against_real_schema(durable_inbox_db):
    stuck_id = _post(durable_inbox_db)
    waiting_id = _post(durable_inbox_db)
    pending_id = _post(durable_inbox_db)

    with durable_inbox_db.cursor() as cur:
        cur.execute("SELECT mark_delivered(%s, %s)", (stuck_id, -1))
        cur.execute("SELECT mark_delivered(%s, %s)", (waiting_id, 3600))
    durable_inbox_db.commit()

    stuck = reconciler.find_stuck_messages(durable_inbox_db)
    assert stuck_id in stuck
    assert waiting_id not in stuck
    assert pending_id not in stuck


def test_find_stuck_messages_is_idempotent(durable_inbox_db):
    stuck_id = _post(durable_inbox_db)
    with durable_inbox_db.cursor() as cur:
        cur.execute("SELECT mark_delivered(%s, %s)", (stuck_id, -1))
    durable_inbox_db.commit()

    first = reconciler.find_stuck_messages(durable_inbox_db)
    second = reconciler.find_stuck_messages(durable_inbox_db)
    assert first == second
    assert stuck_id in first
