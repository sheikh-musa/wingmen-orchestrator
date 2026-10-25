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
