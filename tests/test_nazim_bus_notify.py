"""Tests for nervous_system/nazim_bus_notify.py -- nudge coalescing (held_commitments
#445, audit-20261006 item 5, bus#54648): defer while the console pane is pane_working
(never send-keys C-u into a mid-turn pane and wipe a live draft), and the nudge line
carries a priority breakdown ("P1:1 P3:3"). Pure-logic: tmux/fire_window/pane_busy are
all injected or monkeypatched, no live tmux session or DB.
"""
import os
import sys
from types import SimpleNamespace

NS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nervous_system")
sys.path.insert(0, NS)

import nazim_bus_notify as nbn  # noqa: E402


def _fake_run_factory(session_has=True):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == [nbn.TMUX, "has-session"]:
            return SimpleNamespace(returncode=0 if session_has else 1)
        return SimpleNamespace(returncode=0)

    return calls, fake_run


def test_nudge_sends_and_returns_true_when_pane_idle(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(nbn.subprocess, "run", fake_run)
    monkeypatch.setattr(nbn.fire_window, "is_held", lambda s: False)
    sent = nbn._nudge(3, 1, ["P1", "P3", "P3"], pane_busy=lambda s: False)
    assert sent is True
    send_keys_calls = [c for c in calls if c[1] == "send-keys"]
    assert len(send_keys_calls) == 3  # C-u, the line, Enter
    line_call = send_keys_calls[1]
    assert "3 new fleet bus msg(s)" in line_call[5]
    assert "P1:1 P3:2" in line_call[5]
    assert "(1 need a reply)" in line_call[5]


def test_nudge_defers_without_sending_when_pane_busy(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(nbn.subprocess, "run", fake_run)
    monkeypatch.setattr(nbn.fire_window, "is_held", lambda s: False)
    sent = nbn._nudge(2, 0, ["P2", "P2"], pane_busy=lambda s: True)
    assert sent is False
    assert not any(c[1] == "send-keys" for c in calls), \
        "a busy pane must NEVER receive send-keys -- C-u would wipe a live draft"


def test_nudge_defers_when_fire_window_held(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(nbn.subprocess, "run", fake_run)
    monkeypatch.setattr(nbn.fire_window, "is_held", lambda s: True)
    monkeypatch.setattr(nbn.fire_window, "held_reason", lambda s: "recycle")
    sent = nbn._nudge(1, 0, ["P2"], pane_busy=lambda s: False)
    assert sent is False
    assert not any(c[1] == "send-keys" for c in calls)


def test_nudge_defers_when_no_live_session(monkeypatch):
    calls, fake_run = _fake_run_factory(session_has=False)
    monkeypatch.setattr(nbn.subprocess, "run", fake_run)
    sent = nbn._nudge(1, 0, ["P2"], pane_busy=lambda s: False)
    assert sent is False


def test_nudge_omits_breakdown_bracket_when_no_priorities_given(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(nbn.subprocess, "run", fake_run)
    monkeypatch.setattr(nbn.fire_window, "is_held", lambda s: False)
    nbn._nudge(2, 0, (), pane_busy=lambda s: False)
    line_call = [c for c in calls if c[1] == "send-keys"][1]
    assert "[" not in line_call[5]


def test_nudge_omits_need_a_reply_tail_when_zero(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(nbn.subprocess, "run", fake_run)
    monkeypatch.setattr(nbn.fire_window, "is_held", lambda s: False)
    nbn._nudge(2, 0, ["P2", "P2"], pane_busy=lambda s: False)
    line_call = [c for c in calls if c[1] == "send-keys"][1]
    assert "need a reply" not in line_call[5]


def test_breakdown_line_import_matches_shared_helper():
    from scripts.lib.priority_breakdown import breakdown_line
    assert nbn.breakdown_line is breakdown_line


def test_default_pane_busy_wires_to_shared_pane_busy_definition():
    # the real (non-injected) path _nudge falls back to when no override is given --
    # must resolve to the ONE shared pane-busy definition (agent_wake._pane_busy), not
    # a reimplementation. An unreadable/nonexistent session reads as NOT busy (the
    # "unreadable -> deliver, never silently drop" contract).
    assert nbn._default_pane_busy("definitely-not-a-real-tmux-session-xyz") is False


# ---- bayanqa/encoder COVERAGE-GAP wake-suppression stopgap (bus #75459/#75469/
# #75470, folded into #445's PR per orch-console #76339 so trunk == live checkout,
# no uncommitted drift). Pure query-construction checks via a fake cursor that
# records what SQL + params each call issued -- no real DB. ----
class _FakeCursor:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self._result.pop(0) if isinstance(self._result, list) else self._result

    def fetchall(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def rowcount(self):
        return self._result


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def test_state_max_id_query_excludes_suppressed_pattern():
    cur = _FakeCursor([(42,), (0,)])
    nbn._state(_FakeConn(cur))
    sql, params = cur.calls[0]
    assert "NOT" in sql and nbn._WAKE_SUPPRESS_SQL in sql
    assert params == nbn._WAKE_SUPPRESS_PARAMS


def test_new_priorities_query_excludes_suppressed_pattern():
    cur = _FakeCursor([])
    nbn._new_priorities(_FakeConn(cur), since_id=100)
    sql, params = cur.calls[0]
    assert "NOT" in sql and nbn._WAKE_SUPPRESS_SQL in sql
    assert params == (100, *nbn._WAKE_SUPPRESS_PARAMS)


def test_autoack_suppressed_updates_only_the_suppressed_pattern_and_returns_count():
    cur = _FakeCursor(7)  # rowcount
    n = nbn._autoack_suppressed(_FakeConn(cur), scholar_healthy=lambda c: True)
    sql, params = cur.calls[0]
    assert sql.strip().startswith("UPDATE agent_messages SET read_at=now()")
    assert "read_at IS NULL" in sql
    assert nbn._WAKE_SUPPRESS_SQL in sql
    assert params == nbn._WAKE_SUPPRESS_PARAMS
    assert n == 7


# ---- scholar-health gate on the auto-ack (orch-console #76351): the auto-ack must
# NEVER mask a genuine scholar-down event -- only act while cc-scholar reads as
# confirmed-healthy, and fail toward LEAVING the row unread on any uncertainty. ----
def test_autoack_is_noop_when_scholar_not_confirmed_healthy():
    cur = _FakeCursor(999)  # would be the rowcount if the UPDATE ran
    n = nbn._autoack_suppressed(_FakeConn(cur), scholar_healthy=lambda c: False)
    assert n == 0
    assert cur.calls == [], "no UPDATE must be issued when scholar isn't confirmed healthy"


def test_scholar_confirmed_healthy_true_on_fresh_working_heartbeat():
    import datetime as dt
    now = dt.datetime(2026, 10, 26, 0, 0, 0, tzinfo=dt.timezone.utc)
    cur = _FakeCursor([("working", now - dt.timedelta(seconds=90))])
    assert nbn._scholar_confirmed_healthy(_FakeConn(cur), now=now) is True


def test_scholar_confirmed_healthy_false_when_status_offline():
    import datetime as dt
    now = dt.datetime(2026, 10, 26, 0, 0, 0, tzinfo=dt.timezone.utc)
    cur = _FakeCursor([("offline", now)])
    assert nbn._scholar_confirmed_healthy(_FakeConn(cur), now=now) is False


def test_scholar_confirmed_healthy_false_when_heartbeat_stale():
    import datetime as dt
    now = dt.datetime(2026, 10, 26, 0, 0, 0, tzinfo=dt.timezone.utc)
    stale = now - dt.timedelta(seconds=nbn._SCHOLAR_LIVENESS_GRACE_S + 1)
    cur = _FakeCursor([("working", stale)])
    assert nbn._scholar_confirmed_healthy(_FakeConn(cur), now=now) is False


def test_scholar_confirmed_healthy_false_when_no_matching_row():
    cur = _FakeCursor([])
    assert nbn._scholar_confirmed_healthy(_FakeConn(cur)) is False


def test_scholar_confirmed_healthy_false_on_db_error():
    class _BoomCursor(_FakeCursor):
        def execute(self, *a, **k):
            raise RuntimeError("connection reset")

    assert nbn._scholar_confirmed_healthy(_FakeConn(_BoomCursor([]))) is False
