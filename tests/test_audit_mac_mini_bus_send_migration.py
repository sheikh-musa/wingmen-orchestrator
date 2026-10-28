"""audit_mac_mini.py's post_agent_message() must go through scripts/bus_send.py,
never its own ad-hoc supabase.table("agent_messages").insert() (Musa op#34845,
bus #78757/#79125, PR-B step 2 batch 2).

INDEPENDENT write: audit_mac_mini.py is a manual, interactively-run one-shot
CLI tool (python scripts/audit_mac_mini.py), not a daemon -- each of its 4
call sites posts a standalone status/approval-request message with nothing
else to roll back together. The `supabase` parameter is dropped from
post_agent_message()'s signature since it no longer does any DB write of its
own (bus_send.send() opens its own connection); async because the surrounding
script is async throughout, bridged via asyncio.to_thread so the sync send()
doesn't block the event loop.
"""
import pathlib
import sys

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.audit_mac_mini as amm  # noqa: E402


def test_no_raw_insert_into_agent_messages_remains():
    # Same regex the real gate enforces (scripts/agent_messages_writer_gate.py
    # _TABLE_WRITE_RE): .table("agent_messages").insert(/.upsert( -- a read-only
    # .select( on the same table (the approval poll) is explicitly out of scope.
    from scripts.agent_messages_writer_gate import _TABLE_WRITE_RE, _INSERT_RE
    text = (REPO / "scripts" / "audit_mac_mini.py").read_text()
    assert not _TABLE_WRITE_RE.search(text)
    assert not _INSERT_RE.search(text)


def test_post_agent_message_goes_through_bus_send():
    text = (REPO / "scripts" / "audit_mac_mini.py").read_text()
    assert "from scripts import bus_send" in text or "from scripts.bus_send import" in text


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


@pytest.mark.asyncio
async def test_post_agent_message_posts_a_real_row_through_post_agent_message_rpc(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db)
    msg_id = await amm.post_agent_message(
        subject="subj-for-test", body="body-for-test", requires_response=True,
    )
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, subject, body "
            "FROM agent_messages ORDER BY id"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, subj, body = rows[0]
    assert (from_agent, to_agent, mtype, req) == ("cc-ihsanos", "cai", "update", True)
    assert subj == "subj-for-test"
    assert body == "body-for-test"
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT id FROM agent_messages")
        assert cur.fetchone()[0] == msg_id


@pytest.mark.asyncio
async def test_post_agent_message_default_requires_response_false(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db)
    await amm.post_agent_message(subject="s", body="b")
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT requires_response, priority FROM agent_messages")
        req, prio = cur.fetchone()
    assert req is False
