"""console_alive() wiring in priority_sla_watchdog.py's internal-escalation ladder
(audit-20261006 item 4, bus#54648).

CAI-600 says an agent-queue stall escalates to orch-console (not the operator) since
the operator cannot clear another body's inbox. But if orch-console itself has no fresh
agent_status heartbeat, routing there is a page into a void -- the same failure class
wake_backstop_sweep's dead-foreign page and sre_liveness_watchdog's escalate already
close. _internal_path_alive now only answers "is there an internal party to try at all"
(false only when the recipient IS orch-console itself); escalate_internally is the one
that picks orch-console vs. its console_alive()-gated hub (cc-orchestrator) fallback.
"""
from __future__ import annotations

from scripts import priority_sla_watchdog as s


def _violation(agent: str = "cc-cosem-platform", priority: str = "P1") -> dict:
    return {
        "agent": agent,
        "message_id": 424242,
        "priority": priority,
        "elapsed_minutes": 33,
        "subject": "gate request: residency",
    }


# ── _internal_path_alive: now a pure "is there a non-circular internal party" check ──

def test_internal_path_alive_false_only_for_orch_console_itself():
    assert s._internal_path_alive("orch-console") is False


def test_internal_path_alive_true_for_any_other_recipient():
    assert s._internal_path_alive("cc-cosem-platform") is True
    assert s._internal_path_alive("cai") is True


# ── escalate_internally: console_alive()-gated routing ────────────────────────────────
# escalate_internally takes no injected conn -- it's a standalone write with no
# atomicity requirement, so it correctly uses bus_send.send() (backlog#124, bus
# #74939). Mock send() directly rather than psycopg.connect, which send() never
# reaches first (it resolves its own DSN before opening a connection).

def test_escalate_internally_targets_console_when_alive(monkeypatch):
    sent = []
    monkeypatch.setattr(s, "console_alive", lambda: True)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    ok = s.escalate_internally(_violation(priority="P1"), nudges=2)
    assert ok is True
    kw = sent[0]
    assert kw["to"] == "orch-console"
    assert kw["priority"] == "P1"          # the violation's own priority, unchanged
    assert kw["req"] is False
    assert "console-dead-redirect" not in kw["body"]


def test_escalate_internally_redirects_to_hub_when_console_dead(monkeypatch):
    sent = []
    monkeypatch.setattr(s, "console_alive", lambda: False)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    ok = s.escalate_internally(_violation(priority="P1"), nudges=2)
    assert ok is True
    kw = sent[0]
    assert kw["to"] == "cc-orchestrator"
    assert kw["req"] is True
    assert "audit-20261006" in kw["body"]
    assert "stall" in kw["body"].lower()   # original context preserved


def test_escalate_internally_never_downgrades_a_p0_violation_on_redirect(monkeypatch):
    sent = []
    monkeypatch.setattr(s, "console_alive", lambda: False)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    s.escalate_internally(_violation(priority="P0"), nudges=0)
    assert sent[0]["priority"] == "P0"     # never downgraded to P1


def test_escalate_internally_console_alive_exception_fails_toward_hub_redirect(monkeypatch):
    sent = []

    def boom():
        raise RuntimeError("db blip")

    monkeypatch.setattr(s, "console_alive", boom)
    monkeypatch.setattr(s.bus_send, "send", lambda **kw: (sent.append(kw), (1, "t"))[1])
    ok = s.escalate_internally(_violation(), nudges=0)
    assert ok is True
    assert sent[0]["to"] == "cc-orchestrator"
