"""opus_reprobe_storefront.py (backlog#124, bus #74939): _post() now calls
post_agent_message() directly on its existing cursor instead of a raw INSERT, so the
deadline-escalation loop in main() (3 recipients, one commit at the end) stays atomic.
Runs the real SQL against a real ephemeral Postgres to catch a positional-argument
mistake in the direct RPC call.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import opus_reprobe_storefront as ors  # noqa: E402

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
        """)
        cur.execute(migration_117)
        cur.execute(migration_120)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def test_post_writes_the_row_with_fixed_posted_by_identity(db):
    with db.cursor() as cur:
        ors._post(cur, "cc-fleet-health", "subj", "body text", "P1")
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, subject, body, priority, "
            "requires_response, posted_by_identity FROM agent_messages")
        row = cur.fetchone()
    from_agent, to_agent, mtype, subject, body, priority, req, posted_by_identity = row
    assert from_agent == "cc-fleet-health" and to_agent == "cc-fleet-health"
    assert mtype == "update" and subject == "subj" and body == "body text"
    assert priority == "P1" and req is False
    assert posted_by_identity == "cc-fleet-health"


def test_post_loop_of_three_commits_atomically(db):
    """The deadline-escalation shape in main(): 3 calls on one cursor, one commit."""
    with db.cursor() as cur:
        for who in ("cc-fleet-health", "cai", "orch-console"):
            ors._post(cur, who, "escalation subject", "escalation body", "P1")
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT to_agent FROM agent_messages ORDER BY id")
        recipients = [r[0] for r in cur.fetchall()]
    assert recipients == ["cc-fleet-health", "cai", "orch-console"]
