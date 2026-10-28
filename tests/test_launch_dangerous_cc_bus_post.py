"""scripts/lib/launch_dangerous_cc_bus_post.py -- the bounded, best-effort
agent_messages post used by launch_dangerous_cc.sh's EXIT trap + Vercel
deploy-poller (console review, bus #80132 conditions 1/2):

1. Every post is bounded (~10s) and best-effort: any failure (bad/missing
   DSN, SystemExit, DB down, or exceeding the bound) is swallowed and
   logged to stderr, never raised -- this process must always exit 0 so it
   never changes the EXIT trap's exit status or delays teardown beyond the
   bound.
2. All fields (subject, body, from_agent, etc.) arrive via environment
   variables, never argv -- process listings (`ps aux`) are guard-scanned
   and visible to anyone on the box.

This is this launcher's ONLY remaining raw-write surface (3 call sites in
launch_dangerous_cc.sh, all post-launch: 2 in the Vercel deploy poller, 1 in
the EXIT trap's session-end report) -- migrating all 3 onto one shared,
independently unit-testable helper instead of 3 near-duplicate inline
`python -c` heredocs with bash values interpolated directly into the
source string (the actual argv-exposure bug being fixed here).
"""
import importlib
import os
import pathlib
import sys
import time

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.lib.launch_dangerous_cc_bus_post as ldcc_post  # noqa: E402


def _clear_ldcc_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("LDCC_"):
            monkeypatch.delenv(k, raising=False)


def test_no_raw_insert_into_agent_messages_in_launcher():
    # Same regexes the real gate enforces (scripts/agent_messages_writer_gate.py):
    # .table("agent_messages").insert(/.upsert( and INSERT INTO agent_messages.
    # sys.path.insert(...) and the unrelated sb.table('agents').update(...) (a
    # different table, out of scope here) must NOT trip this.
    from scripts.agent_messages_writer_gate import _TABLE_WRITE_RE, _INSERT_RE
    text = (REPO / "scripts" / "launch_dangerous_cc.sh").read_text()
    assert not _TABLE_WRITE_RE.search(text)
    assert not _INSERT_RE.search(text)


def test_launcher_passes_subject_and_body_via_env_not_argv():
    text = (REPO / "scripts" / "launch_dangerous_cc.sh").read_text()
    # the fix: subject/body must never be interpolated straight into a
    # `python -c "...$subject..."` string (that puts them in argv / ps aux).
    # The one remaining `python -c` (the unrelated agents-table status flip,
    # a different table, out of scope here) carries no subject/body.
    assert "LDCC_SUBJECT=\"$subject\"" in text or 'LDCC_SUBJECT="DEPLOY' in text
    assert "launch_dangerous_cc_bus_post.py" in text
    assert text.count("LDCC_SUBJECT=") == 3
    assert text.count("LDCC_BODY=") == 3


def test_post_swallows_a_hanging_send_within_the_bound(monkeypatch):
    """A send() that never returns must not hang this process past the bound."""
    _clear_ldcc_env(monkeypatch)
    monkeypatch.setenv("LDCC_FROM_BASE", "cc-orchestrator")
    monkeypatch.setenv("LDCC_TO", "cai")
    monkeypatch.setenv("LDCC_TYPE", "update")
    monkeypatch.setenv("LDCC_SUBJECT", "s")
    monkeypatch.setenv("LDCC_BODY", "b")
    monkeypatch.setenv("LDCC_PRIORITY", "P3")
    monkeypatch.setattr(ldcc_post, "_BOUND_SECONDS", 1)

    def _hang(*a, **k):
        time.sleep(30)

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", _hang)

    start = time.monotonic()
    rc = ldcc_post.main()
    elapsed = time.monotonic() - start
    assert rc == 0
    assert elapsed < 5, f"should have been bounded to ~1s, took {elapsed}s"


def test_post_swallows_a_raising_send(monkeypatch):
    _clear_ldcc_env(monkeypatch)
    monkeypatch.setenv("LDCC_FROM_BASE", "cc-orchestrator")
    monkeypatch.setenv("LDCC_TO", "cai")
    monkeypatch.setenv("LDCC_TYPE", "update")
    monkeypatch.setenv("LDCC_SUBJECT", "s")
    monkeypatch.setenv("LDCC_BODY", "b")
    monkeypatch.setenv("LDCC_PRIORITY", "P3")

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", lambda *a, **k: (_ for _ in ()).throw(SystemExit("no DATABASE_URL")))

    assert ldcc_post.main() == 0


def test_post_passes_connect_and_statement_timeout_to_send(monkeypatch):
    # bus #80233/#80236 (coord's PR#591 review, fast-follow): SIGALRM only
    # bounds a connect that fails outright, not one that succeeds and then
    # stalls on a query -- the helper must ask send() for a real libpq-level
    # bound too, not rely on the alarm alone.
    _clear_ldcc_env(monkeypatch)
    monkeypatch.setenv("LDCC_FROM_BASE", "cc-orchestrator")
    monkeypatch.setenv("LDCC_TO", "cai")
    monkeypatch.setenv("LDCC_TYPE", "update")
    monkeypatch.setenv("LDCC_SUBJECT", "s")
    monkeypatch.setenv("LDCC_BODY", "b")
    monkeypatch.setenv("LDCC_PRIORITY", "P3")

    calls = []
    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", lambda *a, **k: calls.append(k))

    assert ldcc_post.main() == 0
    assert len(calls) == 1
    assert calls[0]["connect_timeout"] == 5
    assert calls[0]["statement_timeout_ms"] == 8000


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


def test_post_writes_a_real_row_with_fields_from_env(db, monkeypatch):
    _clear_ldcc_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setenv("LDCC_FROM_BASE", "cc-irsyad-coord")
    monkeypatch.setenv("LDCC_SUB_TAG", "cc-irsyad-coord-1")
    monkeypatch.setenv("LDCC_TO", "cai")
    monkeypatch.setenv("LDCC_TYPE", "update")
    monkeypatch.setenv("LDCC_SUBJECT", "Session completed: orchestrator (12s)")
    monkeypatch.setenv("LDCC_BODY", "Session ended. Outcome: completed.")
    monkeypatch.setenv("LDCC_PRIORITY", "P3")

    assert ldcc_post.main() == 0

    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, sub_tag, to_agent, message_type, priority, "
            "requires_response, subject, body, is_test FROM agent_messages"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, sub_tag, to_agent, mtype, prio, req, subj, body, is_test = rows[0]
    assert (from_agent, sub_tag, to_agent, mtype, prio, req) == (
        "cc-irsyad-coord", "cc-irsyad-coord-1", "cai", "update", "P3", False)
    assert subj == "Session completed: orchestrator (12s)"
    assert body == "Session ended. Outcome: completed."
    assert is_test is False


def test_post_req_true_sets_requires_response(db, monkeypatch):
    _clear_ldcc_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setenv("LDCC_FROM_BASE", "cc-orchestrator")
    monkeypatch.setenv("LDCC_TO", "cai")
    monkeypatch.setenv("LDCC_TYPE", "blocker")
    monkeypatch.setenv("LDCC_SUBJECT", "DEPLOY FAILED")
    monkeypatch.setenv("LDCC_BODY", "detail")
    monkeypatch.setenv("LDCC_PRIORITY", "P1")
    monkeypatch.setenv("LDCC_REQ", "1")

    assert ldcc_post.main() == 0
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT requires_response FROM agent_messages")
        assert cur.fetchone()[0] is True


def test_post_test_mode_env_sets_is_test(db, monkeypatch):
    _clear_ldcc_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setenv("LDCC_FROM_BASE", "cc-reviewer")
    monkeypatch.setenv("LDCC_SUB_TAG", "cc-reviewer-9")
    monkeypatch.setenv("LDCC_TO", "cai")
    monkeypatch.setenv("LDCC_TYPE", "update")
    monkeypatch.setenv("LDCC_SUBJECT", "Session completed: test")
    monkeypatch.setenv("LDCC_BODY", "b")
    monkeypatch.setenv("LDCC_PRIORITY", "P3")
    monkeypatch.setenv("LAUNCH_DANGEROUS_CC_TEST_MODE", "1")

    assert ldcc_post.main() == 0
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT is_test FROM agent_messages")
        assert cur.fetchone()[0] is True
