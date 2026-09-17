"""lane_reply.sh scrubs secrets before they reach the DB log OR the outbound send.

A#9 / D#5 (substrate audit 2026-09-16): lane_reply.sh is the ONLY sender that reaches
CLIENT groups (cosem-exams `direct`; gazzabyte-irsyad / hk-editor / alderei `supervised`),
yet it had no secret_redact and no send_arg_guard — every operator-facing sender does. A
lane pasting a Postgres DSN / bot token into a client reply would ship it unfiltered.

These run the REAL scripts/lane_reply.sh against an EPHEMERAL Postgres and assert a pg-DSN
in the text is redacted in BOTH operator_messages.text AND the reviewer-forwarded DRAFT body
(the supervised path's outbound content). The `direct` path sends the identical scrubbed
$TEXT variable (single redaction point in the wrapper), so proving the DB-writing paths
proves the send payload too.

Local integration test — needs PG17 (WINGMEN_PG17_BIN) and the repo's .venv, the same infra
the migration suite assumes; skips cleanly otherwise. DATABASE_URL is pointed at the throwaway
PG, so nothing ever touches the real substrate.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import psycopg
import pytest

# The repo THIS test lives in (resolves to a worktree too). The script is $HOME-anchored for
# its .venv/helpers (secret_redact, send_arg_guard), which are unchanged across trees.
REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "lane_reply.sh"
HOME_VENV_PY = pathlib.Path(os.path.expanduser("~/wingmen/orchestrator/.venv/bin/python3"))

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54331"

DSN_SECRET = "postgresql://appuser:sup3r-secret-pw@db.internal.example:5432/prod"
MARKER = "[REDACTED:pg-dsn]"

pytestmark = pytest.mark.skipif(
    not (SCRIPT.exists() and HOME_VENV_PY.exists()
         and os.path.exists(os.path.join(PG_BIN, "initdb"))),
    reason="lane_reply integration test needs PG17 (WINGMEN_PG17_BIN) + ~/wingmen/orchestrator/.venv",
)


@pytest.fixture(scope="module")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="lanereply-pg-")
    shutil.rmtree(datadir)  # initdb wants to create it
    sockdir = tempfile.mkdtemp(prefix="lanereply-sock-")
    # LC_ALL=C: Homebrew PG17 on macOS otherwise fails startup with "postmaster became
    # multithreaded during startup" (a mis-set ambient locale trips a fork-safety check).
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
    """Clean schema + the minimal tables + channels lane_reply.sh touches."""
    with psycopg.connect(pg_dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""CREATE TABLE bot_channels (
            channel_key text PRIMARY KEY, token_env_key text,
            allowed_chat_ids bigint[], channel_tag text, group_routing jsonb)""")
        cur.execute("""CREATE TABLE operator_messages (
            id serial PRIMARY KEY, direction text, channel text, chat_id text,
            tag text, text text, delivered boolean, from_name text)""")
        cur.execute("""CREATE TABLE agent_messages (
            id serial PRIMARY KEY, from_agent text, sub_tag text, to_agent text,
            message_type text, subject text, body text, priority text,
            requires_response boolean)""")
        cur.execute("""INSERT INTO bot_channels VALUES
            ('t-supervised','FAKE_TOKEN',ARRAY[999]::bigint[],'tsup',
             '{"agent_phase":"supervised","agent_reviewer":"orch-console"}'::jsonb),
            ('t-drill','FAKE_TOKEN',ARRAY[999]::bigint[],'tdrill',
             '{"agent_phase":"drill"}'::jsonb)""")
    return pg_dsn


def _run(channel, text, dsn):
    # DATABASE_URL points at the throwaway PG; LANE_AGENT_ID attributes the speaker so the
    # script never needs tmux. A fresh env (minus any inherited agent id) keeps it hermetic.
    env = dict(os.environ, DATABASE_URL=dsn, LANE_AGENT_ID="cc-cosem-exams")
    env.pop("CC_AGENT_ID", None)
    env.pop("CC_BASE_AGENT_ID", None)
    return subprocess.run([str(SCRIPT), channel, text],
                          capture_output=True, text=True, timeout=90, env=env)


def test_supervised_reply_redacts_dsn_in_draft_and_log(db):
    r = _run("t-supervised", f"the prod db is {DSN_SECRET} — please migrate", db)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT body FROM agent_messages ORDER BY id DESC LIMIT 1")
        body = cur.fetchone()[0]
        cur.execute("SELECT text FROM operator_messages ORDER BY id DESC LIMIT 1")
        logged = cur.fetchone()[0]
    # the reviewer-forwarded DRAFT (the outbound content) AND the durable log are both scrubbed
    assert DSN_SECRET not in body and MARKER in body, body
    assert DSN_SECRET not in logged and MARKER in logged, logged


def test_drill_reply_redacts_dsn_in_log(db):
    r = _run("t-drill", f"note: {DSN_SECRET} stays internal", db)
    assert r.returncode == 0, r.stderr
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT text FROM operator_messages ORDER BY id DESC LIMIT 1")
        logged = cur.fetchone()[0]
    assert DSN_SECRET not in logged and MARKER in logged, logged


def test_arg_swap_channel_as_text_fails_loud(db):
    # a channel name in the TEXT slot (arg-swap footgun) must abort, not ship
    r = _run("t-drill", "cosem-exams", db)
    assert r.returncode == 2, f"rc={r.returncode} stderr={r.stderr!r}"
    assert "looks like a channel" in r.stderr
