"""test_bus_send.py — the ONE sanctioned bus-post CLI enforces priority (bus #43651).

orch-console's ask after op#22517's aftermath (a hand-written INSERT that
omitted `priority` silently landed at the table default 'P2', below the hub's
wake floor — reference_hub_wake_floor_p1_rr): make it structural, never a
promise. These are PURE-LOGIC / argparse tests — no live DB, no psycopg
connection (matches the style of test_operator_log_scope.py).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import bus_send as bs  # noqa: E402


# ---- --priority is REQUIRED, no default ------------------------------------

def test_priority_is_required():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "update",
            "--subject", "s", "--req",
        ])


def test_priority_rejects_unknown_value():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "update",
            "--subject", "s", "--priority", "URGENT",
        ])


@pytest.mark.parametrize("p", ["P0", "P1", "P2", "P3"])
def test_priority_accepts_each_valid_value(p):
    args = bs.build_parser().parse_args([
        "--to", "cc-orchestrator", "--type", "update",
        "--subject", "s", "--priority", p,
    ])
    assert args.priority == p


def test_message_type_rejects_unknown_value():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "nonsense",
            "--subject", "s", "--priority", "P1",
        ])


# ---- identity resolution: fail closed, never guess --------------------------

def test_identity_prefers_cc_base_agent_id():
    env = {"CC_BASE_AGENT_ID": "cc-substrate", "AGENT_ID": "should-not-win",
           "ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert bs.resolve_from_agent(env) == "cc-substrate"


def test_identity_falls_back_to_agent_id():
    env = {"AGENT_ID": "cc-fleet-health"}
    assert bs.resolve_from_agent(env) == "cc-fleet-health"


def test_identity_uses_orch_agent_id_only_when_console_role():
    env = {"ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert bs.resolve_from_agent(env) == "orch-console"


def test_identity_ignores_orch_agent_id_when_role_is_not_console():
    # ORCH_AGENT_ID is fleet-wide .env noise (every launcher sources .env) —
    # must never be trusted outside the one documented console-fallback case.
    env = {"ORCH_AGENT_ID": "orch-console"}
    with pytest.raises(bs.IdentityError):
        bs.resolve_from_agent(env)


def test_identity_refuses_rather_than_guesses_when_nothing_resolves():
    with pytest.raises(bs.IdentityError):
        bs.resolve_from_agent({})


# ---- empty-body guard (Nazim shipped 3 blank rows on 2026-09-05) -----------

def test_read_body_refuses_short_body():
    import io
    with pytest.raises(SystemExit):
        bs.read_body(io.StringIO("too short"))


def test_read_body_accepts_body_at_or_above_floor():
    import io
    body = "x" * bs._MIN_BODY_BYTES
    assert bs.read_body(io.StringIO(body)) == body


# ---- hub wake-floor warning (bus #44527) — warn, never refuse ---------------
# #44508 landed P1 + requires_response=FALSE and sat unseen for ~14h below the
# hub's wake floor (P0/P1 AND requires_response). commitment_sweeper.py fixes
# the requires_response side; this is the bus_send.py side — a caller who
# posts --to cc-orchestrator --req below P1 gets a stderr warning (not a
# refusal: P2-rr is a legitimate way to post a non-urgent item).

def test_warns_when_hub_bound_req_below_p1(capsys):
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", True, "P2")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "will NOT wake the hub" in err
    assert "P2" in err


def test_no_warning_when_hub_bound_req_at_p1():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", True, "P1", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_when_hub_bound_req_at_p0():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", True, "P0", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_when_not_requires_response():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", False, "P2", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_when_not_addressed_to_hub():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-irsyad", True, "P2", stream=stream)
    assert stream.getvalue() == ""


def test_dry_run_still_warns_below_hub_wake_floor(monkeypatch, capsys):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P2", "--req", "--dry-run",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "will NOT wake the hub" in err


# ---- end-to-end CLI, --dry-run so it never touches the DB -------------------

def test_dry_run_end_to_end(monkeypatch, capsys):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--req", "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "priority=P1" in out
    assert "from=cc-substrate" in out


def test_dry_run_still_enforces_empty_body_guard(monkeypatch):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("too short"))
    with pytest.raises(SystemExit):
        bs.main([
            "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
            "--priority", "P1", "--dry-run",
        ])


def test_main_refuses_when_identity_cannot_resolve(monkeypatch, capsys):
    import io
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--dry-run",
    ])
    assert rc == 2
    assert "cannot resolve" in capsys.readouterr().err


# ---- send() — the one INSERT, reused by _bus_tmp.py / scratchpad shims -----
# (bus #43673). priority validation happens before any DB connection, so
# these are safe to test directly with no live DB.

def test_send_rejects_invalid_priority():
    with pytest.raises(ValueError):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, "URGENT")


def test_send_rejects_missing_priority():
    with pytest.raises(ValueError):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, None)


@pytest.mark.parametrize("p", ["P0", "P1", "P2", "P3"])
def test_send_accepts_valid_priority_then_reaches_the_db_call(monkeypatch, p):
    # Confirms priority validation doesn't reject a valid value — push past
    # it into psycopg2.connect (monkeypatched to fail fast) so no real DB
    # connection is attempted. dsn is passed explicitly so this doesn't
    # depend on DATABASE_URL/.env being present (CI has neither).
    import psycopg2

    def _boom(*a, **k):
        raise RuntimeError("no live DB in tests")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(RuntimeError, match="no live DB in tests"):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, p, dsn="postgresql://unused")


# ---- is_new_ask (op#22669 root-cause fix): the operator_asks link row ------
# closes the 41-day gap (2026-08-17 -> 2026-09-27) where bus_send.py became
# the dominant console-assign write path but never wrote the operator_asks
# half console_assign.py's HTTP path always wrote. Fake psycopg2 connection/
# cursor (mirrors tests/console/test_app.py's _FakeCursor/_FakeConn) so the
# REAL SQL send() builds is exercised, not a stand-in.

class _FakeCursor:
    def __init__(self, fetch_queue):
        self.executed = []
        self._fetch = list(fetch_queue)

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetch.pop(0)


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _fake_send(monkeypatch, fetch_queue):
    import psycopg2
    cur = _FakeCursor(fetch_queue)
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)
    return cur, conn


def test_send_writes_operator_asks_link_for_a_fresh_console_decision(monkeypatch):
    # set_config (no fetchone), directive INSERT ... RETURNING id, thread_id
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4242, "th-uuid-abc")])
    row_id, thread_id = bs.send(
        "orch-console", "cc-irsyad", "decision", "ship the thing",
        "x" * bs._MIN_BODY_BYTES, "P1", req=True, dsn="postgresql://unused",
    )
    assert (row_id, thread_id) == (4242, "th-uuid-abc")
    assert conn.committed is True
    sqls = [e[0] for e in cur.executed]
    assert any("insert into operator_asks" in s.lower() for s in sqls)
    link = next(e for e in cur.executed if "insert into operator_asks" in e[0].lower())
    assert link[1] == ("ship the thing", "th-uuid-abc", "cc-irsyad")
    assert "status" not in link[0].lower()


def test_send_does_not_link_when_not_requires_response(monkeypatch):
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(1, "th-1")])
    bs.send("orch-console", "cc-irsyad", "decision", "s",
            "x" * bs._MIN_BODY_BYTES, "P1", req=False, dsn="postgresql://unused")
    assert not any("insert into operator_asks" in e[0].lower() for e in cur.executed)


def test_send_does_not_link_a_reply(monkeypatch):
    # a reply_to riding an existing thread is not a NEW ask. The looked-up
    # thread_id is a full 36-char uuid so it skips the separate prefix-lookup
    # branch (len(thread) < 36), which would otherwise consume a 3rd fetchone.
    full_thread = "22222222-2222-2222-2222-222222222222"
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(full_thread,), (2, full_thread)])
    bs.send("orch-console", "cc-irsyad", "decision", "s",
            "x" * bs._MIN_BODY_BYTES, "P1", req=True, reply_to=99,
            dsn="postgresql://unused")
    assert not any("insert into operator_asks" in e[0].lower() for e in cur.executed)


def test_send_does_not_link_an_explicit_existing_thread(monkeypatch):
    # a 36-char full uuid thread is used as-is (no prefix-lookup fetchone) —
    # riding an existing thread is not a fresh ask either.
    full_thread = "11111111-1111-1111-1111-111111111111"
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(3, full_thread)])
    bs.send("orch-console", "cc-irsyad", "decision", "s",
            "x" * bs._MIN_BODY_BYTES, "P1", req=True, thread=full_thread,
            dsn="postgresql://unused")
    assert not any("insert into operator_asks" in e[0].lower() for e in cur.executed)


def test_send_does_not_link_non_decision_type(monkeypatch):
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4, "th-4")])
    bs.send("orch-console", "cc-irsyad", "update", "s",
            "x" * bs._MIN_BODY_BYTES, "P1", req=True, dsn="postgresql://unused")
    assert not any("insert into operator_asks" in e[0].lower() for e in cur.executed)


def test_send_does_not_link_when_from_agent_is_not_console(monkeypatch):
    # only the console's own fresh asks are tracked this way — a lane-to-lane
    # decision is not an "operator ask".
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(5, "th-5")])
    bs.send("cc-scholar", "cc-irsyad", "decision", "s",
            "x" * bs._MIN_BODY_BYTES, "P1", req=True, dsn="postgresql://unused")
    assert not any("insert into operator_asks" in e[0].lower() for e in cur.executed)
