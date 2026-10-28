"""reset_cai.sh / reset_fleet_health.sh / reset_nazim.sh's LAYER-2 escalation must
resolve ORCH_DIR (and LOGDIR) from their OWN script location, never from a
hardcoded $HOME/wingmen/orchestrator -- same pattern reset_auditor.sh already used
(bus #78935/#78954: a test process leaked a real "LOUD: reset_cai STUCK" row to
the live substrate because reset_cai.sh hardcoded $HOME/wingmen/orchestrator for
both the bus_send.py path AND LOGDIR, so a background pytest run with the real
$HOME executed the LIVE bus_send.py against the LIVE .env despite the test's own
sandboxing attempt).

Console's ruling (bus #78954) explicitly rejected a redirect hook in favor of (1)
a REFUSAL backstop in bus_send.py (tested in tests/test_bus_send.py's
test_refuse_if_sandboxed_but_live_* / test_dburl_*_sandboxed* tests) and (2) these
scripts resolving their own path self-relatively, same as reset_auditor.sh. This
file covers (2): with ORCH_DIR now self-relative, pointing it at THIS repo (which
has no .env file -- confirmed by test_dburl_resolves_from_the_passed_string in
test_bus_send.py) is enough to prove isolation -- no fake-$HOME sandbox needed
at all.

STEP 2 FOLLOW-UP (bus #78886/#78899/#78953, op#34845 PR-B step 2): a static
grep-clean check alone doesn't prove the migrated bus_send.py CLI invocation
actually posts. This file's own 3-script coverage above already proves it for
reset_cai.sh/reset_fleet_health.sh/reset_nazim.sh; reset_auditor.sh (already
self-relative, needed no .sh change) gets the matching executed proof below.
"""
import os
import pathlib
import re
import subprocess

import psycopg
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
LIVE_VENV_PY = pathlib.Path(os.path.expanduser("~/wingmen/orchestrator/.venv/bin/python3"))

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54336"

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(PG_BIN, "initdb")) or not LIVE_VENV_PY.exists(),
    reason="reset-family self-relative-path test needs PG17 (WINGMEN_PG17_BIN) + "
           "~/wingmen/orchestrator/.venv (this test repo has no .venv of its own, "
           "so the script's own interpreter fallback needs the live one for psycopg2)",
)


@pytest.fixture(scope="module")
def pg_dsn():
    import shutil
    import tempfile
    import time

    datadir = tempfile.mkdtemp(prefix="resetfam-srp-pg-")
    shutil.rmtree(datadir)
    sockdir = tempfile.mkdtemp(prefix="resetfam-srp-sock-")
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


def _extract(script_path: pathlib.Path, start: str, end_re: str) -> str:
    text = script_path.read_text()
    i = text.index(start)
    m = re.search(end_re, text[i:])
    assert m, f"end marker not found after {start!r} in {script_path}"
    return text[i: i + m.end()]


def _count_rows(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        return cur.fetchone()[0]


# orch-console #80174: NO test may post a real escalation row, not even to an ephemeral DB.
# These used to assert a row LANDED; that same shape, run from a scratch clone with the live
# env file symlinked in, paged the live bus as "LOUD: reset_cai STUCK" (#78935, #80158). The
# escalation now refuses in any test context, so the proof is: refused, logged, nothing posted.
# The real post path's argv is covered without a DB in tests/test_reset_escalation_guard.py.
@pytest.mark.parametrize("script_name,sess_env", [
    ("reset_cai", {"SESS": "cai"}),
    ("reset_nazim", {"SESS": "nazim"}),
    ("reset_fleet_health", {}),
])
def test_escalation_refuses_in_a_test_context_and_posts_nothing(script_name, sess_env, db, tmp_path):
    script = REPO / "scripts" / f"{script_name}.sh"
    snippet = _extract(script, "_escalated=0", r"\nfi\n")
    env = dict(
        os.environ, ORCH_DIR=str(REPO), DATABASE_URL=db, WINGMEN_TEST_SANDBOX="1",
        LOGDIR=str(tmp_path),
    )
    env.update(sess_env)
    r = subprocess.run(["bash", "-c", snippet],
                        capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 8, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "REFUSED to post the operator-relay row: WINGMEN_TEST_SANDBOX is set" in r.stderr
    assert "escalated LOUD failure" not in r.stderr
    assert "escalate to the operator MANUALLY" not in r.stderr and "escalate MANUALLY" not in r.stderr
    assert _count_rows(db) == 0, "a test posted an escalation row"
    assert f"{script_name} escalation REFUSED (test context)" in (tmp_path / "reset_escalation_refused.log").read_text()


def test_reset_auditor_escalation_refuses_in_a_test_context_and_posts_nothing(db, tmp_path):
    # Extracted from the VERIFY-failed branch, skipping the preceding composer-pane retry
    # loop (it calls composer_parse_pane, not defined in this snippet); _cleared=0 is set
    # directly instead.
    script = REPO / "scripts" / "reset_auditor.sh"
    snippet = _extract(script, 'if [ "$_cleared" != 1 ]; then', r"\n  exit 8\nfi\n")
    preamble = (
        '_cleared=0\n'
        'PY="$ORCH_DIR/.venv/bin/python3"; [ -x "$PY" ] || PY="$HOME/wingmen/orchestrator/.venv/bin/python3"\n'
    )
    env = dict(
        os.environ, ORCH_DIR=str(REPO), DATABASE_URL=db, WINGMEN_TEST_SANDBOX="1",
        RESET_BY="cc-fleet-health", BASE="cai", LOGDIR=str(tmp_path),
    )
    r = subprocess.run(["bash", "-c", preamble + snippet],
                        capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 8, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "REFUSED to post the operator-relay row" in r.stderr
    assert "escalated LOUD failure" not in r.stderr
    assert _count_rows(db) == 0, "a test posted an escalation row"


@pytest.mark.parametrize("name", ["reset_cai.sh", "reset_fleet_health.sh", "reset_nazim.sh"])
def test_no_hardcoded_home_orchestrator_path_remains(name):
    text = (REPO / "scripts" / name).read_text()
    assert "$HOME/wingmen/orchestrator/scripts/bus_send.py" not in text, name
    assert "$HOME/wingmen/orchestrator/logs" not in text, name
    assert "ORCH_DIR=" in text and "_SCRIPTS_DIR/.." in text, name
