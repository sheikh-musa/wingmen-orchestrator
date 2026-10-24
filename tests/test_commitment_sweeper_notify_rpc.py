"""commitment_sweeper._notify() (backlog#124, bus #74939): now calls
post_agent_message() directly on its existing cursor instead of a raw INSERT, so
each notify stays atomic with the held_commitments UPDATE that follows it and with
the rest of sweep()'s batch (one commit for the whole due-list). Runs the real SQL
against a real ephemeral Postgres to catch a positional-argument mistake.
"""
import pathlib
import sys

import psycopg
from psycopg.rows import dict_row
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import nervous_system.commitment_sweeper as cs  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def db(pg_dsn):
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_120 = (MIGRATIONS_DIR / "118_post_agent_message_optional_params.sql").read_text()
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
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE held_commitments (
                id         BIGSERIAL PRIMARY KEY,
                status     TEXT NOT NULL,
                fired_at   TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            );
            INSERT INTO held_commitments (id, status) VALUES (1, 'pending');
        """)
        cur.execute(migration_117)
        cur.execute(migration_120)

    conn = psycopg.connect(pg_dsn, row_factory=dict_row)
    yield conn
    conn.close()


def test_notify_writes_the_row_with_the_right_shape(db):
    with db.cursor() as cur:
        mid = cs._notify(cur, "cc-orchestrator", "subj", "body text", priority="P1")
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, subject, body, priority, "
            "requires_response FROM agent_messages WHERE id=%s", (mid,))
        row = cur.fetchone()
    assert row["from_agent"] == cs.FROM_AGENT and row["to_agent"] == "cc-orchestrator"
    assert row["message_type"] == "update" and row["subject"] == "subj"
    assert row["priority"] == "P1" and row["requires_response"] is True


def test_notify_and_held_commitments_update_commit_together(db):
    """The property this refactor preserves: a notify + its status UPDATE land in
    the same transaction as sweep() intends (one commit for the whole due-list)."""
    with db.cursor() as cur:
        mid = cs._notify(cur, "cc-orchestrator", "subj", "body", priority="P1")
        cur.execute(
            "UPDATE held_commitments SET status='fired', fired_at=now(), "
            "updated_at=now() WHERE id=%s AND status='pending'", (1,))
    db.commit()

    with db.cursor() as cur:
        cur.execute("SELECT status FROM held_commitments WHERE id=1")
        status = cur.fetchone()["status"]
        cur.execute("SELECT id FROM agent_messages WHERE id=%s", (mid,))
        row = cur.fetchone()
    assert status == "fired"
    assert row is not None
