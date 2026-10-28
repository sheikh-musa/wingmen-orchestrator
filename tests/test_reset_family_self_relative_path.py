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


def _last_row(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, subject "
            "FROM agent_messages ORDER BY id DESC LIMIT 1"
        )
        return cur.fetchone()


@pytest.mark.parametrize("script_name,sess_env,expected_from", [
    ("reset_cai", {"SESS": "cai"}, "cc-fleet-health"),
    ("reset_nazim", {"SESS": "nazim"}, "cc-fleet-health"),
    ("reset_fleet_health", {}, "orch-console"),
])
def test_escalation_posts_a_real_row_with_orch_dir_pointed_at_a_dotenv_less_repo(
    script_name, sess_env, expected_from, db
):
    # WINGMEN_TEST_SANDBOX=1 proves the (1) backstop never even fires for a
    # correctly self-relative script -- the escalation must succeed normally,
    # not get refused.
    script = REPO / "scripts" / f"{script_name}.sh"
    snippet = _extract(script, "_escalated=0", r"\nfi\n")
    env = dict(
        os.environ, ORCH_DIR=str(REPO), DATABASE_URL=db, WINGMEN_TEST_SANDBOX="1",
    )
    env.update(sess_env)
    r = subprocess.run(["bash", "-c", snippet],
                        capture_output=True, text=True, timeout=30, env=env)
    # exit 8 is this branch's own always-fire exit code, same as reset_auditor.sh's
    # equivalent test -- the real proof is the DB row, not the exit code.
    assert r.returncode == 8, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "escalated LOUD failure" in r.stderr, f"stderr={r.stderr!r}"
    row = _last_row(db)
    assert row is not None, "no row landed -- bus_send invocation did not post"
    from_agent, to_agent, mtype, prio, req = row[0], row[1], row[2], row[3], row[4]
    assert (from_agent, to_agent, mtype, prio, req) == (expected_from, "orch-console", "blocker", "P1", True)


def test_reset_auditor_escalation_posts_a_real_row_with_orch_dir_pointed_at_a_dotenv_less_repo(db):
    # reset_auditor.sh was already self-relative (ORCH_DIR override + HOME-
    # fallback PY, same pattern the other 3 scripts were fixed to match) --
    # no .sh change needed here, just the executed-invocation proof console's
    # bus #78886/#78899 asked for (a static grep-clean check alone doesn't
    # prove the migrated bus_send.py CLI call actually posts). Extracted from
    # the VERIFY-failed branch, skipping the preceding composer-pane retry
    # loop (which calls composer_parse_pane, not defined in this snippet) --
    # that loop is irrelevant to proving the escalation call itself, so
    # _cleared=0 is set directly instead of exercised through the loop.
    script = REPO / "scripts" / "reset_auditor.sh"
    snippet = _extract(script, 'if [ "$_cleared" != 1 ]; then', r"\n  exit 8\nfi\n")
    preamble = (
        '_cleared=0\n'
        'PY="$ORCH_DIR/.venv/bin/python3"; [ -x "$PY" ] || PY="$HOME/wingmen/orchestrator/.venv/bin/python3"\n'
    )
    env = dict(
        os.environ, ORCH_DIR=str(REPO), DATABASE_URL=db, WINGMEN_TEST_SANDBOX="1",
        RESET_BY="cc-fleet-health", BASE="cai",
    )
    r = subprocess.run(["bash", "-c", preamble + snippet],
                        capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 8, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "escalated LOUD failure" in r.stderr, f"stderr={r.stderr!r}"
    row = _last_row(db)
    assert row is not None, "no row landed -- bus_send invocation did not post"
    from_agent, to_agent, mtype, prio, req = row[0], row[1], row[2], row[3], row[4]
    assert (from_agent, to_agent, mtype, prio, req) == ("cc-fleet-health", "orch-console", "blocker", "P1", True)


@pytest.mark.parametrize("name", ["reset_cai.sh", "reset_fleet_health.sh", "reset_nazim.sh"])
def test_no_hardcoded_home_orchestrator_path_remains(name):
    text = (REPO / "scripts" / name).read_text()
    assert "$HOME/wingmen/orchestrator/scripts/bus_send.py" not in text, name
    assert "$HOME/wingmen/orchestrator/logs" not in text, name
    assert "ORCH_DIR=" in text and "_SCRIPTS_DIR/.." in text, name
