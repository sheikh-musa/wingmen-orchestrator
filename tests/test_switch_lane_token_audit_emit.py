"""switch_lane_token.sh's identity-stamped audit + break-glass alert (cai CAI-RESP-747
conds 2/3) must go through scripts/bus_send.py, never a raw psycopg INSERT INTO
agent_messages (Musa op#34845, bus #78757/#78846, PR-B step 2).

No existing test touched this PYAUDIT heredoc at all before this change -- it has no
tmux/pane dependency (pure env-var-driven Python + DB), so unlike the reset_*.sh
family's LAYER-2 branch this one IS dynamically testable. This test extracts the
EXACT heredoc body between the script's own `<<'PYAUDIT'` / `PYAUDIT` markers and
runs it as a real subprocess against an ephemeral Postgres, so it proves the actual
shipped text, not a hand-copied duplicate that could drift from the real file.
"""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "switch_lane_token.sh"
VENV_PY = pathlib.Path(os.path.expanduser("~/wingmen/orchestrator/.venv/bin/python3"))

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54332"

pytestmark = pytest.mark.skipif(
    not (SCRIPT.exists() and VENV_PY.exists() and os.path.exists(os.path.join(PG_BIN, "initdb"))),
    reason="switch_lane_token audit-emit test needs PG17 (WINGMEN_PG17_BIN) + ~/wingmen/orchestrator/.venv",
)


def _extract_pyaudit() -> str:
    text = SCRIPT.read_text()
    m = re.search(r"<<'PYAUDIT'.*?\n(.*?)\nPYAUDIT\n", text, re.DOTALL)
    assert m, "PYAUDIT heredoc markers not found in switch_lane_token.sh"
    return m.group(1)


@pytest.fixture(scope="module")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="switchtoken-pg-")
    shutil.rmtree(datadir)
    sockdir = tempfile.mkdtemp(prefix="switchtoken-sock-")
    pgenv = dict(os.environ, LC_ALL="C")
    subprocess.run([os.path.join(PG_BIN, "initdb"), "-D", datadir, "-U", "postgres",
                    "--auth=trust", "--locale=C", "--encoding=UTF8"],
                   check=True, capture_output=True, env=pgenv)
    subprocess.run([os.path.join(PG_BIN, "pg_ctl"), "-D", datadir,
                    "-l", os.path.join(datadir, "log"),
                    "-o", f"-p {PORT} -k {sockdir} -c listen_addresses='' -c timezone=UTC",
                    "-w", "start"], check=True, capture_output=True, env=pgenv)
    dsn = f"host={sockdir} port={PORT} user=postgres dbname=postgres"
    for _ in range(50):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.OperationalError:
            time.sleep(0.1)
    try:
        yield dsn
    finally:
        subprocess.run([os.path.join(PG_BIN, "pg_ctl"), "-D", datadir, "-w", "stop"],
                       capture_output=True)
        shutil.rmtree(datadir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)


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
        # thread_id's own column DEFAULT gen_random_uuid() means a raw INSERT (the
        # pre-migration code) ALSO ends up with a non-NULL thread_id -- that alone
        # does not discriminate the two paths here (unlike test_lane_reply_
        # redaction.py's hand-rolled schema, which has no such default). This side
        # table is the real discriminator: only post_agent_message's body writes to
        # it, so its row count proves the RPC actually ran, independent of any
        # column-default coincidence.
        cur.execute("CREATE TABLE post_agent_message_calls (id bigserial PRIMARY KEY)")
        # Same minimal post_agent_message RPC shape as migration 122 / the other
        # bus_send.py-migration tests in this suite (test_lane_reply_redaction.py).
        cur.execute("""CREATE FUNCTION post_agent_message(
            p_from_agent TEXT, p_to_agent TEXT, p_message_type TEXT, p_subject TEXT,
            p_body TEXT, p_priority TEXT, p_requires_response BOOLEAN, p_thread_id UUID,
            p_sub_tag TEXT, p_is_test BOOLEAN DEFAULT FALSE, p_posted_by_identity TEXT DEFAULT NULL
        ) RETURNS TABLE(id BIGINT, thread_id UUID) LANGUAGE plpgsql AS $fn$
        BEGIN
            INSERT INTO post_agent_message_calls DEFAULT VALUES;
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


def _run_pyaudit(env_overrides, dsn):
    env = dict(os.environ, DATABASE_URL=dsn)
    env.update(env_overrides)
    return subprocess.run(
        [str(VENV_PY), "-"], input=_extract_pyaudit(), capture_output=True, text=True,
        cwd=str(REPO), env=env, timeout=30,
    )


def _rows(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, "
            "thread_id FROM agent_messages ORDER BY id"
        )
        return cur.fetchall()


def _rpc_call_count(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM post_agent_message_calls")
        return cur.fetchone()[0]


BASE_ENV = {
    "SWITCH_ACTOR": "musa", "SWITCH_SESS": "cosem-exams",
    "SWITCH_BEFORE": "fp-old", "SWITCH_AFTER": "fp-new", "SWITCH_TARGET": "fp-new",
    "SWITCH_MODELAPPLY": "0",
}


def test_routine_pass_switch_emits_one_quiet_update_to_console_through_rpc(db):
    r = _run_pyaudit({**BASE_ENV, "SWITCH_RESULT": "PASS", "SWITCH_BREAKGLASS": "0",
                       "SWITCH_ARMED": "0"}, db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    rows = _rows(db)
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, thread_id = rows[0]
    assert (from_agent, to_agent, mtype, prio, req) == ("cc-fleet-health", "orch-console", "update", "P3", False)
    assert _rpc_call_count(db) == 1, "row did not go through post_agent_message"


def test_fail_result_escalates_to_loud_blocker_even_without_armed_or_breakglass(db):
    r = _run_pyaudit({**BASE_ENV, "SWITCH_RESULT": "FAIL", "SWITCH_BREAKGLASS": "0",
                       "SWITCH_ARMED": "0"}, db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    rows = _rows(db)
    assert len(rows) == 1, rows
    assert (rows[0][2], rows[0][3]) == ("blocker", "P1")


def test_break_glass_alerts_both_orch_console_and_cai(db):
    r = _run_pyaudit({**BASE_ENV, "SWITCH_RESULT": "PASS", "SWITCH_BREAKGLASS": "1",
                       "SWITCH_ARMED": "0"}, db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    rows = _rows(db)
    recipients = sorted(row[1] for row in rows)
    assert recipients == ["cai", "orch-console"], rows
    assert all((row[2], row[3]) == ("blocker", "P1") for row in rows), rows
    assert _rpc_call_count(db) == 2, "both rows must go through post_agent_message"


def test_armed_switch_also_alerts_cai(db):
    r = _run_pyaudit({**BASE_ENV, "SWITCH_RESULT": "PASS", "SWITCH_BREAKGLASS": "0",
                       "SWITCH_ARMED": "1"}, db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    recipients = sorted(row[1] for row in _rows(db))
    assert recipients == ["cai", "orch-console"], recipients
