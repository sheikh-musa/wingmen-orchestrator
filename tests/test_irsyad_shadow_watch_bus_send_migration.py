"""irsyad_shadow_watch.py's bus() must go through scripts/bus_send.py, never
its own ad-hoc psycopg INSERT INTO agent_messages (Musa op#34845, bus
#78757/#79125, PR-B step 2 irsyad_*.py migration).

bus() is INDEPENDENT (opens its own connection every call, best-effort --
"a broken bus must not take the watcher down") -- send() is a behavior-
preserving drop-in, not a redesign.
"""
import pathlib
import sys

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.irsyad_shadow_watch as sw  # noqa: E402


def test_no_raw_insert_into_agent_messages_remains():
    text = (REPO / "scripts" / "irsyad_shadow_watch.py").read_text()
    assert "INSERT INTO agent_messages" not in text


def test_bus_goes_through_bus_send():
    text = (REPO / "scripts" / "irsyad_shadow_watch.py").read_text()
    assert "from scripts.bus_send import send" in text


def test_bus_is_still_best_effort_on_failure(monkeypatch):
    # A broken bus must not raise out of bus() -- the watcher keeps polling.
    # An unreachable-but-configured DSN raises a real connection error (not
    # SystemExit, which pre-dates this migration -- _dsn()/dburl() both
    # sys.exit() on a genuinely UNSET DATABASE_URL, unchanged by this PR).
    scheme = "postgresql://"
    unreachable_dsn = scheme + "u:p@127.0.0.1:1/nope?connect_timeout=1"
    monkeypatch.setenv("DATABASE_URL", unreachable_dsn)
    sw.bus("subject", "body")  # must not raise


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


def test_bus_posts_a_real_row_through_post_agent_message_rpc(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db)
    sw.bus("subj-for-test", "body-for-test", priority="P1")
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, subject, body, priority "
            "FROM agent_messages ORDER BY id"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, subj, body, prio = rows[0]
    assert (from_agent, to_agent, mtype, prio) == ("substrate", "orch-console", "update", "P1")
    assert subj == "subj-for-test"
    assert body == "body-for-test"
