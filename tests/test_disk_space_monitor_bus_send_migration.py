"""disk_space_monitor.sh's EMERGENCY bus_nudge() must go through scripts/bus_send.py,
never a raw psycopg INSERT INTO agent_messages (Musa op#34845, bus #78757/#78846,
PR-B step 2).

Static discriminator (test_no_raw_insert_or_psycopg_remains /
test_emergency_nudge_goes_through_bus_send_py) plus a real executed-invocation
test (console's bus #78886/#78916: a static grep-clean check alone doesn't
prove the call actually posts).

bus_nudge()'s heredoc resolves ORCH_DIR self-relatively (bus #78935/#78954/
#78963, same pattern the reset_*.sh family uses) instead of hardcoding
`~/wingmen/orchestrator` -- ORCH_DIR is passed into the heredoc's own
environment explicitly (it is never exported earlier in this script) and both
the .env read and the `scripts.bus_send` import resolve through it. The
executed test below points ORCH_DIR at a throwaway scratch directory with its
own .env (pointing at an ephemeral PG) and `scripts/` symlinked in from this
repo -- no fake-$HOME sandbox needed at all, same technique as
tests/test_reset_family_self_relative_path.py.
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
SCRIPT = REPO / "scripts" / "disk_space_monitor.sh"
LIVE_VENV_PY = pathlib.Path(os.path.expanduser("~/wingmen/orchestrator/.venv/bin/python3"))

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54337"


def test_no_raw_insert_or_psycopg_remains():
    text = SCRIPT.read_text()
    assert "INSERT INTO agent_messages" not in text
    assert "import psycopg2" not in text


def test_emergency_nudge_goes_through_bus_send_py():
    text = SCRIPT.read_text()
    assert "from scripts.bus_send import send as bus_send" in text
    assert "bus_send('orch-console', to_agent, 'blocker', subj, body, pri" in text


def test_dsn_and_import_resolve_via_orch_dir_not_hardcoded_home():
    text = SCRIPT.read_text()
    assert "os.path.expanduser('~/wingmen/orchestrator" not in text
    assert "os.environ['ORCH_DIR']" in text
    assert 'ORCH_DIR="$ORCH_DIR"' in text


@pytest.fixture(scope="module")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="dsm-pg-")
    shutil.rmtree(datadir)
    sockdir = tempfile.mkdtemp(prefix="dsm-sock-")
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


@pytest.fixture
def scratch_orch_dir(db):
    """A throwaway ORCH_DIR -- never $HOME -- with its own .env (pointing at the
    ephemeral PG) and scripts/ symlinked in from this repo (bus_nudge()'s heredoc
    imports scripts.bus_send, which itself imports scripts.lib.agent_identity at
    module load time, so the whole package must be present, not just one file)."""
    d = tempfile.mkdtemp(prefix="dsm-orchdir-")
    orch = pathlib.Path(d)
    os.symlink(REPO / "scripts", orch / "scripts")
    (orch / ".env").write_text(f"DATABASE_URL={db}\n")
    try:
        yield str(orch)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _extract_py() -> str:
    text = SCRIPT.read_text()
    m = re.search(r"<<'PY' 2>>\"\$LOG\"\n(.*?)\nPY\n", text, re.DOTALL)
    assert m, "<<'PY' heredoc markers not found in disk_space_monitor.sh"
    return m.group(1)


@pytest.mark.skipif(
    not (LIVE_VENV_PY.exists() and os.path.exists(os.path.join(PG_BIN, "initdb"))),
    reason="disk_space_monitor executed bus-send test needs PG17 (WINGMEN_PG17_BIN) + ~/wingmen/orchestrator/.venv",
)
def test_emergency_nudge_posts_a_real_row_through_post_agent_message_rpc(scratch_orch_dir, db):
    env = dict(os.environ, ORCH_DIR=scratch_orch_dir, WINGMEN_TEST_SANDBOX="1")
    r = subprocess.run(
        [str(LIVE_VENV_PY), "-", "P1", "subj-from-arg", "body-from-arg", "orch-console"],
        input=_extract_py(), capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, subject, body "
            "FROM agent_messages ORDER BY id"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, subj, body = rows[0]
    assert (from_agent, to_agent, mtype, prio, req) == (
        "orch-console", "orch-console", "blocker", "P1", False)
    assert subj == "subj-from-arg"
    assert body == "body-from-arg"
