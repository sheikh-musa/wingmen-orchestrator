"""auto_recycle_on_bloat.py (backlog#124, bus #74939): warn_escalate()/warn_console()
now call post_agent_message() directly on their existing cursor (via the shared
_post_on_cursor() helper) instead of a raw INSERT, so a WARN batch across multiple
lanes/recipients still commits atomically (one commit after the loop, today: all WARNs
in a pass land together or not at all). Runs the real SQL against a real ephemeral
Postgres to catch a positional-argument mistake in the direct RPC call.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scripts.auto_recycle_on_bloat as arb  # noqa: E402

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


def test_post_on_cursor_writes_the_expected_row(db):
    with db.cursor() as cur:
        arb._post_on_cursor(cur, "cc-fleet-health", "orch-console", "subj", "body text", "P2")
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, subject, body, priority, "
            "requires_response FROM agent_messages")
        row = cur.fetchone()
    from_agent, to_agent, mtype, subject, body, priority, req = row
    assert from_agent == "cc-fleet-health" and to_agent == "orch-console"
    assert mtype == "update" and subject == "subj" and body == "body text"
    assert priority == "P2" and req is False


def test_warn_batch_shape_commits_atomically(db):
    """The property this refactor preserves: warn_escalate()/warn_console() loop over
    multiple (lane, recipient) pairs on ONE cursor with ONE commit at the end."""
    with db.cursor() as cur:
        for to_agent in ("cc-fleet-health", "orch-console"):
            arb._post_on_cursor(cur, "cc-fleet-health", to_agent, "WARN subj", "WARN body", "P2")
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT to_agent FROM agent_messages ORDER BY id")
        recipients = [r[0] for r in cur.fetchall()]
    assert recipients == ["cc-fleet-health", "orch-console"]


def test_warn_batch_rolls_back_fully_on_mid_batch_failure(db, monkeypatch):
    """Mirrors warn_escalate()/warn_console()'s real shape: `with psycopg.connect(dsn)
    as c, c.cursor() as cur:` -- psycopg rolls back automatically on exception exit
    from that `with` block, which is the actual atomicity guarantee in production."""
    real_execute = psycopg.Cursor.execute
    calls = {"n": 0}

    def flaky_execute(self, query, *a, **kw):
        q = query if isinstance(query, str) else str(query)
        if "post_agent_message" in q.lower():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on the 2nd WARN")
        return real_execute(self, query, *a, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", flaky_execute)
    with pytest.raises(RuntimeError):
        with psycopg.connect(db.info.dsn) as c, c.cursor() as cur:
            for to_agent in ("cc-fleet-health", "orch-console", "cai"):
                arb._post_on_cursor(cur, "cc-fleet-health", to_agent, "s", "b", "P2")
            c.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        count = cur.fetchone()[0]
    assert count == 0, "a mid-batch failure must roll back the whole WARN batch"
