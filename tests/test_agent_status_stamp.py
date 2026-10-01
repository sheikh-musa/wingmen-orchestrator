"""agent_status_stamp — the lane identity stamp + heartbeat writer (2026-10-01).

Root cause it closes: launch_dangerous_cc.sh stamped host/tmux_session/boot model
ONCE in a silent `except: pass` one-shot, and its heartbeat touched only
last_heartbeat. cc-irsyad-2 + cc-irsyad-coord-1 booted on gzb during the
2026-10-01 pooler breaker, lost the stamp, and stayed host=NULL /
tmux_session=NULL / current_task='session-launch' with fresh heartbeats.

Locks: (1) NULL/empty never overwrites a populated host/session (SQL COALESCE,
proved against a real in-memory SQL engine, not just a string match); (2) every
beat re-asserts host/session so a lost boot stamp self-heals; (3) the boot model
string fills only the bare placeholder of a non-offline row; (4) GUC in the same
txn; (5) the launcher actually calls this writer in BOTH places.
"""
import importlib
import pathlib
import re
import sqlite3

st = importlib.import_module("scripts.lib.agent_status_stamp")
ROOT = pathlib.Path(__file__).resolve().parents[1]


# --- a tiny SQL harness: run the SHIPPED BOOT_SQL / BEAT_SQL on sqlite ------------
# sqlite supports COALESCE/NULLIF/CASE; translate the psycopg named params and the
# Postgres-only bits (`::text`, now()) so the exact shipped statements execute.
def _sqlite_conn(row):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE agent_status (agent_id TEXT PRIMARY KEY, status TEXT, "
              "current_task TEXT, tmux_session TEXT, host TEXT, auth_account TEXT, "
              "auth_fp TEXT, last_heartbeat TEXT, updated_at TEXT)")
    cols = ",".join(row)
    c.execute(f"INSERT INTO agent_status ({cols}) VALUES ({','.join('?' * len(row))})",
              list(row.values()))
    return c


def _run(sql, params, row):
    c = _sqlite_conn(row)
    q = re.sub(r"%\((\w+)\)s", r":\1", sql).replace("::text", "").replace("now()", "'NOW'")
    c.execute(q, params)
    cur = c.execute("SELECT * FROM agent_status")
    names = [d[0] for d in cur.description]
    return dict(zip(names, cur.fetchone()))


LIVE_ROW = {"agent_id": "cc-irsyad-1", "status": "working",
            "current_task": "session-launch model=claude-opus-4-8 repo=wt-worker1",
            "tmux_session": "irsyad-worker-1", "host": "gzbai",
            "auth_account": "musa", "auth_fp": "e1dfa48eec85",
            "last_heartbeat": "OLD", "updated_at": "OLD"}

LOST_STAMP_ROW = {"agent_id": "cc-irsyad-2", "status": "working",
                  "current_task": "session-launch", "tmux_session": None, "host": None,
                  "auth_account": None, "auth_fp": None,
                  "last_heartbeat": "OLD", "updated_at": "OLD"}


def test_beat_with_blank_values_never_nulls_populated_host_or_session():
    p = st.build_params("cc-irsyad-1", host="", session="", model="", repo="")
    out = _run(st.BEAT_SQL, p, LIVE_ROW)
    assert out["host"] == "gzbai"
    assert out["tmux_session"] == "irsyad-worker-1"
    assert out["current_task"] == LIVE_ROW["current_task"]
    assert out["auth_fp"] == "e1dfa48eec85"
    assert out["last_heartbeat"] == "NOW"


def test_boot_with_blank_host_session_never_nulls_populated_values():
    p = st.build_params("cc-irsyad-1", host="", session="", model="claude-opus-4-8", repo="r")
    out = _run(st.BOOT_SQL, p, LIVE_ROW)
    assert out["host"] == "gzbai" and out["tmux_session"] == "irsyad-worker-1"


def test_beat_self_heals_a_lost_boot_stamp():
    """The exact live defect: a fresh-heartbeat row with NULL host/session and the
    bare placeholder gets host + session + the model boot string on the next beat."""
    p = st.build_params("cc-irsyad-2", host="gzbai", session="irsyad-tabung-jumaat",
                        model="claude-opus-4-8", repo="ihsanos-irsyad.wt-tabung-jumaat",
                        auth_fp="e1dfa48eec85")
    out = _run(st.BEAT_SQL, p, LOST_STAMP_ROW)
    assert out["host"] == "gzbai"
    assert out["tmux_session"] == "irsyad-tabung-jumaat"
    assert out["current_task"] == ("session-launch model=claude-opus-4-8 "
                                   "repo=ihsanos-irsyad.wt-tabung-jumaat")
    assert out["auth_fp"] == "e1dfa48eec85"  # fill-only of a missing fp


def test_beat_never_clobbers_a_real_task_or_an_existing_fp():
    row = dict(LIVE_ROW, current_task="building PR #828")
    p = st.build_params("cc-irsyad-1", host="gzbai", session="irsyad-worker-1",
                        model="claude-sonnet-5", repo="x", auth_fp="ffffffffffff")
    out = _run(st.BEAT_SQL, p, row)
    assert out["current_task"] == "building PR #828"
    assert out["auth_fp"] == "e1dfa48eec85"


def test_beat_never_resurrects_an_offline_row_task():
    row = dict(LOST_STAMP_ROW, status="offline", current_task=None)
    p = st.build_params("cc-irsyad-2", host="gzbai", session="s", model="claude-opus-4-8")
    out = _run(st.BEAT_SQL, p, row)
    assert out["current_task"] is None


def test_boot_task_is_none_without_a_model_never_invented():
    assert st.boot_task("", "repo") is None
    assert st.boot_task(None, None) is None
    assert st.boot_task("claude-opus-4-8", "r") == "session-launch model=claude-opus-4-8 repo=r"


class _Cur:
    def __init__(self, log):
        self.log, self.rowcount = log, 1

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, log, fail=None):
        self.log, self.fail, self.committed = log, fail, False

    def cursor(self):
        if self.fail:
            raise self.fail
        return _Cur(self.log)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_write_sets_identity_guc_in_same_txn_before_update():
    log = []
    st.write(_Conn(log), "beat", st.build_params("cc-irsyad-2", host="gzbai"))
    assert "set_config('app.current_agent_id'" in log[0][0] and log[0][1] == ("cc-irsyad-2",)
    assert log[1][0] == st.BEAT_SQL


def test_run_retries_transient_failure_then_succeeds():
    log, calls, sleeps = [], [], []

    def connect(dsn, connect_timeout=None):
        calls.append(dsn)
        if len(calls) == 1:
            return _Conn(log, fail=RuntimeError("ECIRCUITBREAKER too many auth failures"))
        return _Conn(log)

    ok = st.run("boot", st.build_params("cc-x-1", model="m"), retries=3,
                connect=connect, sleep=sleeps.append, dsn="postgresql://local/test")
    assert ok is True and len(calls) == 2 and sleeps == [5]


def test_run_never_retries_an_auth_failure(capsys):
    calls = []

    def connect(dsn, connect_timeout=None):
        calls.append(dsn)
        raise RuntimeError('FATAL: password authentication failed for user "postgres"')

    ok = st.run("beat", st.build_params("cc-x-1"), retries=3, connect=connect,
                sleep=lambda s: None, dsn="postgresql://local/test")
    assert ok is False and len(calls) == 1          # no pooler hammering
    assert "write FAILED" in capsys.readouterr().err  # LOUD, not swallowed


def test_launcher_uses_the_shared_writer_for_boot_and_beat():
    src = (ROOT / "scripts" / "launch_dangerous_cc.sh").read_text()
    assert "agent_status_stamp.py\" --mode boot" in src
    assert "agent_status_stamp.py\" --mode beat" in src
    # the old silent inline host stamp is gone
    assert "host = NULLIF(%s,'')" not in src
    # the loop starts AFTER the values it captures are computed
    assert src.index('CC_HOST="$(') < src.index("_heartbeat_loop &")
    assert src.index("--mode boot") < src.index("_heartbeat_loop &")
