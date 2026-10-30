"""Tests for nervous_system/fleet_health_bus_notify.py -- op#35929 (Musa
zero-waste directive, bus #83575/#83579): a wake must carry an unread item or
it does not fire. _nudge(unread) must skip the delegate call entirely (never
send-keys via lane_nudge.sh) when the live recount passed in is 0, the same
guard already proven in nazim_bus_notify (#649). Pure-logic: subprocess is
monkeypatched, no live tmux session or DB.
"""
import os
import sys
from types import SimpleNamespace

NS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nervous_system")
sys.path.insert(0, NS)

import fleet_health_bus_notify as fhbn  # noqa: E402


def _fake_run_factory(rc=0):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=rc, stdout="", stderr="")

    return calls, fake_run


def test_nudge_skips_delegate_and_returns_false_when_unread_zero(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(fhbn.subprocess, "run", fake_run)
    sent = fhbn._nudge(0)
    assert sent is False
    assert calls == [], "a zero-unread recount must never reach lane_nudge.sh"


def test_nudge_calls_delegate_and_returns_true_when_unread_nonzero(monkeypatch):
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(fhbn.subprocess, "run", fake_run)
    sent = fhbn._nudge(5)
    assert sent is True
    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "bash"
    assert args[1] == fhbn.LANE_NUDGE
    assert args[2] == fhbn.SESSION
    assert "5 new bus msg(s)" in args[3]


def test_nudge_returns_false_when_delegate_raises(monkeypatch):
    def fake_run(args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(fhbn.subprocess, "run", fake_run)
    sent = fhbn._nudge(1)
    assert sent is False


def test_nudge_message_never_says_zero_new(monkeypatch):
    """Regression guard for the exact bug op#35929 flagged: a literal
    '0 new bus msg(s)' nudge landing in the pane."""
    calls, fake_run = _fake_run_factory()
    monkeypatch.setattr(fhbn.subprocess, "run", fake_run)
    fhbn._nudge(0)
    assert not calls
