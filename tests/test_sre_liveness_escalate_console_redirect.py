"""sre_liveness_watchdog._escalate's console_alive() wiring (audit-20261006 item 4,
bus#54648). The operator-direct page (nazim_send.sh) is UNCHANGED either way -- "a safety
page is never silenced by assuming a relay is alive." Only the bus row's `to_agent`
switches between orch-console (alive) and the hub cc-orchestrator (dead), so the hub
also gets a durable, attributable record instead of orch-console being relayed into a
void.
"""
from __future__ import annotations

import psycopg
import pytest

import scripts.sre_liveness_watchdog as s


class _CaptureCursor:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=None):
        self.store.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _CaptureConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _CaptureCursor(self.store)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def no_real_page(monkeypatch):
    # the operator-direct nazim_send call must never actually shell out in a unit test.
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: None)


def _insert_params(store):
    return next(p for sql, p in store if "INSERT INTO agent_messages" in sql)


def test_escalate_addresses_console_when_alive(monkeypatch, no_real_page):
    store = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(s, "console_alive", lambda: True)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _CaptureConn(store))
    s._escalate("ep1", "wedge-staged", 2)
    params = _insert_params(store)
    assert params[1] == "orch-console"


def test_escalate_redirects_to_hub_when_console_dead(monkeypatch, no_real_page):
    store = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(s, "console_alive", lambda: False)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _CaptureConn(store))
    s._escalate("ep1", "wedge-staged", 2)
    params = _insert_params(store)
    assert params[1] == "cc-orchestrator"


def test_escalate_still_pages_operator_directly_regardless_of_console_liveness(monkeypatch):
    # the direct nazim_send page must fire in BOTH cases -- it is the independent safety
    # net, never conditioned on console_alive().
    store = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _CaptureConn(store))
    calls = []
    monkeypatch.setattr(s.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    for alive in (True, False):
        calls.clear()
        monkeypatch.setattr(s, "console_alive", lambda alive=alive: alive)
        s._escalate("ep1", "wedge-staged", 2)
        assert calls, f"operator page did not fire when console_alive={alive}"


def test_escalate_console_alive_exception_fails_toward_hub_redirect(monkeypatch, no_real_page):
    store = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")

    def boom():
        raise RuntimeError("db blip")

    monkeypatch.setattr(s, "console_alive", boom)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _CaptureConn(store))
    s._escalate("ep1", "wedge-staged", 2)
    params = _insert_params(store)
    assert params[1] == "cc-orchestrator"
