"""sre_liveness_watchdog._escalate's console_alive() wiring (audit-20261006 item 4,
bus#54648). The operator-direct page (nazim_send.sh) is UNCHANGED either way -- "a safety
page is never silenced by assuming a relay is alive." Only the bus row's `to_agent`
switches between orch-console (alive) and the hub cc-orchestrator (dead), so the hub
also gets a durable, attributable record instead of orch-console being relayed into a
void.
"""
from __future__ import annotations

import pytest

import scripts.sre_liveness_watchdog as s


@pytest.fixture
def no_real_page(monkeypatch):
    # the operator-direct nazim_send call must never actually shell out in a unit test.
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: None)


# _escalate takes no injected conn -- it's a standalone write with no atomicity
# requirement, so it correctly uses bus_send.send() (backlog#124, bus #74939). Mock
# send() directly rather than psycopg.connect, which send() never reaches first (it
# resolves its own DSN before opening a connection).

def test_escalate_addresses_console_when_alive(monkeypatch, no_real_page):
    sent = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(s, "console_alive", lambda: True)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    s._escalate("ep1", "wedge-staged", 2)
    assert sent[0]["to"] == "orch-console"


def test_escalate_redirects_to_hub_when_console_dead(monkeypatch, no_real_page):
    sent = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(s, "console_alive", lambda: False)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    s._escalate("ep1", "wedge-staged", 2)
    assert sent[0]["to"] == "cc-orchestrator"


def test_escalate_still_pages_operator_directly_regardless_of_console_liveness(monkeypatch):
    # the direct nazim_send page must fire in BOTH cases -- it is the independent safety
    # net, never conditioned on console_alive().
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (1, "t"))
    calls = []
    monkeypatch.setattr(s.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    for alive in (True, False):
        calls.clear()
        monkeypatch.setattr(s, "console_alive", lambda alive=alive: alive)
        s._escalate("ep1", "wedge-staged", 2)
        assert calls, f"operator page did not fire when console_alive={alive}"


def test_escalate_console_alive_exception_fails_toward_hub_redirect(monkeypatch, no_real_page):
    sent = []
    monkeypatch.setattr(s, "_dsn", lambda: "postgres://fake")

    def boom():
        raise RuntimeError("db blip")

    monkeypatch.setattr(s, "console_alive", boom)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    s._escalate("ep1", "wedge-staged", 2)
    assert sent[0]["to"] == "cc-orchestrator"
