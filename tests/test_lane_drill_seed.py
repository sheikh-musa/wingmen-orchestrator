"""lane_drill_seed.py (backlog#124, bus #74939): seed() now calls post_agent_message()
directly on its existing cursor (migration 120's is_test param) instead of a raw INSERT,
preserving the single-commit-at-the-end batch across all drill items. These tests run
the real SQL against a real ephemeral Postgres -- the risk worth catching here is a
positional-argument mistake in the direct RPC call, which py_compile can't see.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import lane_drill_seed as lds  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def db(pg_dsn, monkeypatch):
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
            CREATE TABLE agents (id TEXT PRIMARY KEY);
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
            INSERT INTO agents (id) VALUES ('cc-irsyad');
        """)
        cur.execute(migration_117)
        cur.execute(migration_120)

    monkeypatch.setattr(lds, "_dsn", lambda: pg_dsn)
    yield pg_dsn


def _rows(dsn, to_agent):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, subject, body, priority, "
            "requires_response, is_test FROM agent_messages WHERE to_agent=%s ORDER BY id",
            (to_agent,))
        return cur.fetchall()


def test_seed_writes_one_row_per_item_stamped_is_test(db):
    items = [{"from": "Gazzabyte group", "text": "when's my course start"},
              {"from": "Gazzabyte group", "text": "can I reschedule"}]
    ids = lds.seed("cc-irsyad", items)
    assert len(ids) == 2
    rows = _rows(db, "cc-irsyad")
    assert len(rows) == 2
    for row in rows:
        from_agent, to_agent, mtype, subject, body, priority, req, is_test = row
        assert from_agent == "orch-console" and to_agent == "cc-irsyad"
        assert mtype == "question" and priority == "P2" and req is True
        assert is_test is True, "drill rows must be stamped is_test=true (structural isolation)"
        assert lds.MARKER in body


def test_seed_unknown_agent_exits(db):
    with pytest.raises(SystemExit):
        lds.seed("cc-does-not-exist", [{"from": "x", "text": "y"}])


def test_seed_batch_is_atomic_all_or_nothing(db, monkeypatch):
    """The property this refactor is supposed to preserve: a failure partway through
    the batch must leave NOTHING committed, not a partial drill."""
    items = [{"from": "a", "text": "first item ok"}, {"from": "b", "text": "second item"}]

    real_execute = psycopg.Cursor.execute
    calls = {"n": 0}

    def flaky_execute(self, query, *a, **kw):
        q = query if isinstance(query, str) else str(query)
        if "post_agent_message" in q.lower():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on the 2nd item")
        return real_execute(self, query, *a, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", flaky_execute)
    with pytest.raises(RuntimeError):
        lds.seed("cc-irsyad", items)

    assert _rows(db, "cc-irsyad") == [], "a mid-batch failure must roll back the whole batch"


def test_announce_posts_to_the_hub(db):
    mid = lds.announce("cc-irsyad", "Routine drill.")
    with psycopg.connect(db) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_agent, message_type, priority FROM agent_messages WHERE id=%s", (mid,))
        to_agent, mtype, priority = cur.fetchone()
    assert to_agent == "cc-orchestrator" and mtype == "update" and priority == "P2"
