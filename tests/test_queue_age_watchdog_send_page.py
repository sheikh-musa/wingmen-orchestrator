"""queue_age_watchdog.py (backlog#124, bus #74939): _send_queue_page() now calls
post_agent_message() directly on its existing cursor instead of a raw INSERT, so its
own documented all-or-nothing contract ("Returns True only if ALL owner rows
committed") stays true. Runs the real SQL against a real ephemeral Postgres to catch
a positional-argument mistake in the direct RPC call.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scripts.queue_age_watchdog as qaw  # noqa: E402

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
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute(migration_117)
        cur.execute(migration_120)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def test_send_queue_page_pages_each_owner_and_commits_once(db):
    target = {"id": 42, "priority": "P1", "age_min": 35, "family": "coord_dispatch",
              "title": "fix the thing", "owners": ["cc-fleet-health", "orch-console"]}
    ok = qaw._send_queue_page(db, target)
    assert ok is True
    with db.cursor() as cur:
        cur.execute("SELECT to_agent, from_agent, message_type, priority, requires_response "
                    "FROM agent_messages ORDER BY id")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["cc-fleet-health", "orch-console"]
    for to_agent, from_agent, mtype, priority, req in rows:
        assert from_agent == "cc-fleet-health"
        assert mtype == qaw.PAGE_MESSAGE_TYPE
        assert priority == "P1"
        assert req is False


def test_send_queue_page_failure_rolls_back_all_owners(db, monkeypatch):
    target = {"id": 7, "priority": "P2", "age_min": 10, "family": "x", "title": "y",
              "owners": ["cc-fleet-health", "orch-console"]}

    real_execute = psycopg.Cursor.execute
    calls = {"n": 0}

    def flaky_execute(self, query, *a, **kw):
        q = query if isinstance(query, str) else str(query)
        if "post_agent_message" in q.lower():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on the 2nd owner")
        return real_execute(self, query, *a, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", flaky_execute)
    ok = qaw._send_queue_page(db, target)
    assert ok is False, "a mid-batch failure must be reported as a failure, not silently swallowed"
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 0, "a mid-batch failure must roll back ALL owners, not just the failed one"
