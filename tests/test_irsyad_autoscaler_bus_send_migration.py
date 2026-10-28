"""irsyad_autoscaler.py's emit_spin_proposal() must go through scripts/bus_send.py,
never its own ad-hoc raw INSERT INTO agent_messages (Musa op#34845, bus
#78757/#79125, PR-B step 2 irsyad_*.py migration).

emit_spin_proposal() is INDEPENDENT: write_log_row() (the INERT decision log)
already commits on its own, before the proposal check even runs -- there is no
shared transaction left to ride by the time emit_spin_proposal() fires. send()
is a behavior-preserving drop-in, not a redesign. The `conn` parameter is
dropped from emit_spin_proposal()'s signature since it no longer does any DB
write of its own (send() opens its own connection).
"""
import pathlib
import sys

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.irsyad_autoscaler as asc  # noqa: E402


def test_no_raw_insert_into_agent_messages_remains():
    text = (REPO / "scripts" / "irsyad_autoscaler.py").read_text()
    assert "INSERT INTO agent_messages" not in text


def test_proposal_goes_through_bus_send():
    text = (REPO / "scripts" / "irsyad_autoscaler.py").read_text()
    assert "from scripts.bus_send import send" in text


def test_emit_spin_proposal_failure_is_swallowed_not_raised(monkeypatch):
    # Pre-existing contract: a bus failure must return None, never raise
    # (the caller just skips the "posted" print and keeps ticking).
    import scripts.bus_send as bs

    def _boom(*a, **k):
        raise RuntimeError("bus down")

    monkeypatch.setattr(bs, "send", _boom)
    d = asc.Decision(
        coord_queue_depth=3, coord_queue_source="coord_dispatch_queue",
        coord_queue_readable=True, option2_count=0, pool_size=0, demand=3,
        would_spin=True, spin_target="propose +1", would_kill=[], interlocks={},
        reason="demand=3", ambiguous=False, host="test-host",
    )
    assert asc.emit_spin_proposal(d) is None


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


def test_emit_spin_proposal_posts_a_real_row_through_post_agent_message_rpc(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db)
    d = asc.Decision(
        coord_queue_depth=3, coord_queue_source="coord_dispatch_queue",
        coord_queue_readable=True, option2_count=0, pool_size=1, demand=3,
        would_spin=True, spin_target="propose +1", would_kill=[], interlocks={"x": "y"},
        reason="demand=3 pool=1/4", ambiguous=False, host="test-host",
    )
    new_id = asc.emit_spin_proposal(d)
    assert new_id is not None
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, subject "
            "FROM agent_messages ORDER BY id"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, subj = rows[0]
    assert (from_agent, to_agent, mtype, prio, req) == (
        "cc-orchestrator", "orch-console", "question", "P1", True)
    assert subj.startswith(asc.PROPOSAL_SUBJECT_PREFIX)
