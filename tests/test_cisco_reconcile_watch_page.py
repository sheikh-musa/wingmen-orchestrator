"""cisco_reconcile_watch.py (backlog#124, bus #74939): _page() now calls
post_agent_message() directly on its existing cursor instead of a raw INSERT, so
run_once()'s batch (first-reconcile page + every stuck-row page, one commit at the
end) stays atomic. Runs the real SQL against a real ephemeral Postgres to catch a
positional-argument mistake in the direct RPC call.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scripts.cisco_reconcile_watch as w  # noqa: E402

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


def test_page_posts_to_every_recipient(db, monkeypatch):
    monkeypatch.setattr(w, "DRY", False)
    with db.cursor() as cur:
        w._page(cur, "subj", "body", "P1")
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT from_agent, to_agent, message_type, priority, requires_response "
                    "FROM agent_messages ORDER BY id")
        rows = cur.fetchall()
    assert [r[1] for r in rows] == list(w.RECIPIENTS)
    for from_agent, to_agent, mtype, priority, req in rows:
        assert from_agent == w.SRE_FROM and mtype == "update" and priority == "P1" and req is True


def test_page_batch_rolls_back_fully_on_mid_batch_failure(db, monkeypatch):
    """Mirrors run_once()'s real shape: `with psycopg.connect(dsn) as c, c.cursor() as
    cur:` -- a failure partway through a multi-event pass must leave nothing posted."""
    monkeypatch.setattr(w, "DRY", False)
    real_execute = psycopg.Cursor.execute
    calls = {"n": 0}

    def flaky_execute(self, query, *a, **kw):
        q = query if isinstance(query, str) else str(query)
        if "post_agent_message" in q.lower():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on the 2nd recipient")
        return real_execute(self, query, *a, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", flaky_execute)
    with pytest.raises(RuntimeError):
        with psycopg.connect(db.info.dsn) as c, c.cursor() as cur:
            w._page(cur, "first event subj", "b", "P1")
            w._page(cur, "second event subj", "b", "P1")
            c.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        count = cur.fetchone()[0]
    assert count == 0, "a mid-batch failure must roll back the whole pass"
