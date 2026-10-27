"""render_gate.sh's loud_warn() best-effort console alert must go through
scripts/bus_send.py, never a literal `psql -c "...INSERT..."` call (Musa op#34845,
bus #78757/#78846, PR-B step 2).

No existing test touched this. Unlike disk_space_monitor.sh, this heredoc's DSN
comes directly from $DATABASE_URL (no hardcoded live-.env parsing), so it IS
dynamically testable with a plain env override -- extracts the real heredoc text
between the script's own `<<'PY'`/`PY` markers and runs it against an ephemeral
Postgres, so it proves the actual shipped text, not a hand-copied duplicate.
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
SCRIPT = REPO / "scripts" / "render_gate" / "render_gate.sh"
VENV_PY = pathlib.Path(os.path.expanduser("~/wingmen/orchestrator/.venv/bin/python3"))

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54333"

pytestmark = pytest.mark.skipif(
    not (SCRIPT.exists() and VENV_PY.exists() and os.path.exists(os.path.join(PG_BIN, "initdb"))),
    reason="render_gate loud_warn bus-send test needs PG17 (WINGMEN_PG17_BIN) + ~/wingmen/orchestrator/.venv",
)


def _extract_py() -> str:
    text = SCRIPT.read_text()
    m = re.search(r"<<'PY' >/dev/null 2>&1 \|\| true\n(.*?)\nPY\n", text, re.DOTALL)
    assert m, "<<'PY' heredoc markers not found in render_gate.sh"
    return m.group(1)


@pytest.fixture(scope="module")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="rendergate-pg-")
    shutil.rmtree(datadir)
    sockdir = tempfile.mkdtemp(prefix="rendergate-sock-")
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
        # Real discriminator: the old raw INSERT already set posted_by_identity to
        # the same literal 'cc-fleet-health', and thread_id's own column DEFAULT
        # means even a raw INSERT gets a non-NULL one -- neither distinguishes the
        # two paths here. Only post_agent_message's body writes to this table.
        cur.execute("CREATE TABLE post_agent_message_calls (id bigserial PRIMARY KEY)")
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


def _run_loud_warn_py(dsn):
    env = dict(os.environ, DATABASE_URL=dsn)
    return subprocess.run(
        [str(VENV_PY), "-", "subj-from-arg", "body-from-arg"],
        input=_extract_py(), capture_output=True, text=True, timeout=30, env=env,
    )


def test_loud_warn_alert_goes_through_post_agent_message_rpc(db):
    r = _run_loud_warn_py(db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, "
            "posted_by_identity, subject, body FROM agent_messages ORDER BY id"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, posted_by, subj, body = rows[0]
    assert (from_agent, to_agent, mtype, prio, req, posted_by) == (
        "cc-fleet-health", "orch-console", "update", "P2", True, "cc-fleet-health")
    assert subj == "subj-from-arg"
    assert body == "body-from-arg"
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM post_agent_message_calls")
        assert cur.fetchone()[0] == 1, "row did not go through post_agent_message"
