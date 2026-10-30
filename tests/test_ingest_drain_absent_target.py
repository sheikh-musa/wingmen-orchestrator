"""drain_stale_deferred must not 'force-nudge' a tmux session that doesn't exist on this host
(orch-console #77913, 2026-10-27). On the Mini the log-and-route target 'orch' doesn't exist
(the hub lives on gzb), so brandit-coffeemedia's 3 unhandled rows were "nudged" into nothing
for 10 days while the log claimed delivery."""
from datetime import datetime, timezone

import pytest

from nervous_system import ingest


class _Cur:
    def __init__(self, rows):
        self.rows = list(rows)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): pass
    def fetchone(self): return self.rows.pop(0)


class _Conn:
    def __init__(self, n=3, newest_id=31447):
        self.n, self.newest_id = n, newest_id
    def cursor(self):
        return _Cur([(self.n, datetime(2026, 10, 17, tzinfo=timezone.utc), self.newest_id),
                     (True,)])


class _Ch:
    def __init__(self, key="brandit-coffeemedia", mode="log-and-route", inject_target=None):
        self.key, self.channel_tag, self.mode, self.inject_target = key, key, mode, inject_target


@pytest.fixture
def env(monkeypatch):
    logs, nudges = [], []
    monkeypatch.setattr(ingest, "_log_line", logs.append)
    monkeypatch.setattr(ingest, "nudge_session",
                        lambda t, k, n, **kw: nudges.append((t, k, n, kw)) or True)
    monkeypatch.setattr(ingest, "_ABSENT_TARGET_LOGGED", set())
    return logs, nudges


def test_absent_target_is_not_nudged_and_is_logged_once(env, monkeypatch):
    logs, nudges = env
    monkeypatch.setattr(ingest, "_session_exists", lambda t: False)
    ch = _Ch()
    ingest.drain_stale_deferred(_Conn(), ch)
    ingest.drain_stale_deferred(_Conn(), ch)
    assert nudges == []
    assert len(logs) == 1 and "'orch' is not a session on this host" in logs[0]
    assert getattr(ch, "_last_forced_nudge_id", 0) == 0, "an un-nudged batch must not be marked"


def test_present_target_is_nudged_and_logged_as_forced(env, monkeypatch):
    logs, nudges = env
    monkeypatch.setattr(ingest, "_session_exists", lambda t: True)
    ingest.drain_stale_deferred(_Conn(), _Ch())
    assert nudges == [("orch", "brandit-coffeemedia", 3, {"urgent": True})]
    assert "FORCED nudge (max-latency cap)" in logs[0]


def test_failed_delivery_is_logged_as_not_delivered(env, monkeypatch):
    logs, _ = env
    monkeypatch.setattr(ingest, "_session_exists", lambda t: True)
    monkeypatch.setattr(ingest, "nudge_session", lambda t, k, n, **kw: False)
    ingest.drain_stale_deferred(_Conn(), _Ch())
    assert "NOT delivered" in logs[0]


def test_agent_session_channel_uses_its_inject_target(env, monkeypatch):
    logs, nudges = env
    seen = []
    monkeypatch.setattr(ingest, "_session_exists", lambda t: seen.append(t) or True)
    ingest.drain_stale_deferred(_Conn(), _Ch(key="nazim-console", mode="agent-session",
                                             inject_target="nazim"))
    assert seen == ["nazim"] and nudges[0][0] == "nazim"


def test_session_exists_false_for_empty_target():
    assert ingest._session_exists(None) is False and ingest._session_exists("") is False
