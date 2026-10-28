"""irsyad_spin_worker.py's _log_bus() must go through scripts/bus_send.py,
never its own ad-hoc raw INSERT INTO agent_messages (Musa op#34845, bus
#78757/#79125, PR-B step 2 irsyad_*.py migration).

_log_bus() is INDEPENDENT in practice already: _register_pool_row() commits
(releasing the IRSYAD_WORKER_SLOT_ALLOC advisory xact lock) BEFORE _boot()
runs the actual worker-boot subprocess; _log_bus() then posts afterward, in a
fresh transaction, about a boot that has already physically happened. There
is no shared transaction left to ride. send() is a behavior-preserving
drop-in, not a redesign. The `conn` parameter is dropped from _log_bus()'s
signature since it no longer does any DB write of its own.
"""
import pathlib
import sys

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.irsyad_spin_worker as spw  # noqa: E402


def test_no_raw_insert_into_agent_messages_remains():
    text = (REPO / "scripts" / "irsyad_spin_worker.py").read_text()
    assert "INSERT INTO agent_messages" not in text


def test_log_bus_goes_through_bus_send():
    text = (REPO / "scripts" / "irsyad_spin_worker.py").read_text()
    assert "from scripts.bus_send import send" in text


@pytest.fixture
def db(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""CREATE TABLE agent_messages (
            id bigserial PRIMARY KEY, from_agent text, sub_tag text, to_agent text,
            message_type text, subject text, body text, priority text,
            requires_response boolean, thread_id uuid DEFAULT gen_random_uuid() NOT NULL,
            is_test boolean DEFAULT false, posted_by_identity text,
            created_at timestamptz DEFAULT now())""")
        cur.execute("""CREATE FUNCTION post_agent_message(
            p_from_agent TEXT, p_to_agent TEXT, p_message_type TEXT, p_subject TEXT,
            p_body TEXT, p_priority TEXT, p_requires_response BOOLEAN, p_thread_id UUID,
            p_sub_tag TEXT, p_is_test BOOLEAN DEFAULT FALSE, p_posted_by_identity TEXT DEFAULT NULL
        ) RETURNS TABLE(id BIGINT, thread_id UUID) LANGUAGE plpgsql AS $fn$
        BEGIN
            RETURN QUERY
            INSERT INTO agent_messages
                (from_agent, to_agent, message_type, subject, body, priority,
                 requires_response, thread_id, sub_tag, is_test, posted_by_identity)
            VALUES
                (p_from_agent, p_to_agent, p_message_type, p_subject, p_body, p_priority,
                 p_requires_response, p_thread_id, p_sub_tag, p_is_test, p_posted_by_identity)
            RETURNING agent_messages.id, agent_messages.thread_id;
        END;
        $fn$""")
    return pg_dsn


def test_log_bus_posts_a_real_row_through_post_agent_message_rpc(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db)
    spw._log_bus(3, "Booted irsyad-worker-3 ...")
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, subject, body "
            "FROM agent_messages ORDER BY id"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, subj, body = rows[0]
    assert (from_agent, to_agent, mtype, prio, req) == (
        "cc-orchestrator", "orch-console", "update", "P3", False)
    assert subj == "SPUN cc-irsyad worker (pool slot 3) — claiming from coord_dispatch_queue"
    assert body == "Booted irsyad-worker-3 ..."
