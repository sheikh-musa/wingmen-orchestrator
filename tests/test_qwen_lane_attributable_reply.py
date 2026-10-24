"""nervous_system/qwen_lane.py (backlog#124, bus #74939): _attributable_reply() now
calls post_agent_message() directly on its existing cursor (migration 120's is_test
param) instead of a raw INSERT, so it keeps committing atomically with
_mark_handled()'s UPDATE in drain_once() (G1: every row this sandbox writes must be
is_test=true). Runs the real SQL against a real ephemeral Postgres to catch a
positional-argument mistake in the direct RPC call.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import nervous_system.qwen_lane as ql  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def db(pg_dsn):
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_120 = (MIGRATIONS_DIR / "120_post_agent_message_optional_params.sql").read_text()
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='service_role'")
        if cur.fetchone() is None:
            cur.execute("CREATE ROLE service_role")
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
                is_test            BOOLEAN NOT NULL DEFAULT false,
                posted_by_identity TEXT,
                read_at            TIMESTAMPTZ,
                responded_at       TIMESTAMPTZ,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute(migration_117)
        cur.execute(migration_120)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def test_attributable_reply_stamps_is_test_true_and_threads_correctly(db):
    inbound = {"id": 1, "from_agent": "cc-irsyad", "subject": "hello",
               "thread_id": "99999999-9999-9999-9999-999999999999", "is_test": True}
    reply_id = ql._attributable_reply(db, inbound, "the model's answer")
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, thread_id, message_type, subject, body, "
            "requires_response, priority, is_test FROM agent_messages WHERE id=%s",
            (reply_id,))
        row = cur.fetchone()
    from_agent, to_agent, thread_id, mtype, subject, body, req, priority, is_test = row
    assert from_agent == ql.SANDBOX_AGENT_ID and to_agent == "cc-irsyad"
    assert str(thread_id) == inbound["thread_id"], "reply must thread to the inbound request"
    assert mtype == "update" and priority == "P3" and req is False
    assert is_test is True, "G1: every row this sandbox writes must be stamped is_test=true"
    assert "the model's answer" in body


def test_attributable_reply_and_mark_handled_commit_together(db):
    """The property this refactor preserves: drain_once() commits the reply AND the
    read_at/responded_at UPDATE in the same transaction -- a failure between them must
    roll both back, never leave an orphan reply with the inbound still unmarked."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, thread_id, message_type, "
            "subject, body, priority, requires_response, is_test) VALUES "
            "('cc-irsyad', %s, gen_random_uuid(), 'question', 'subj', 'body', 'P2', false, true) "
            "RETURNING id, thread_id", (ql.SANDBOX_AGENT_ID,))
        mid, thread_id = cur.fetchone()
    db.commit()

    inbound = {"id": mid, "from_agent": "cc-irsyad", "subject": "subj",
               "thread_id": str(thread_id), "is_test": True}
    reply_id = ql._attributable_reply(db, inbound, "answer")
    ql._mark_handled(db, mid)
    db.commit()

    with db.cursor() as cur:
        cur.execute("SELECT read_at, responded_at FROM agent_messages WHERE id=%s", (mid,))
        read_at, responded_at = cur.fetchone()
    assert read_at is not None and responded_at is not None
    assert reply_id is not None
