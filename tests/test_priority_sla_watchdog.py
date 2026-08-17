"""Focused tests for priority_sla_watchdog's pool-cap suppression (Nazim #25930).

A POOL-CAPPED recipient (pane shows the CC weekly-limit banner) is exhausted and correctly
WAITING for its weekly reset — its unread/unresponded is benign; a nudge/page can't help. The
SLA watchdog must SKIP it (self-clears at reset). The detector FAILS TOWARD ACTION: any doubt
returns False so a genuine stall is never hidden.
"""
import subprocess

from scripts import priority_sla_watchdog as s


class _R:
    def __init__(self, rc=0, out=""):
        self.returncode = rc
        self.stdout = out


def test_recipient_capped_detects_the_weekly_limit_banner(monkeypatch):
    def fake(cmd, **k):
        if "has-session" in cmd:
            return _R(0)
        return _R(0, "prior output\nYou've hit your weekly limit · resets 5am (Asia/Singapore)\n")
    monkeypatch.setattr(subprocess, "run", fake)
    assert s._recipient_capped("irsyad") is True


def test_recipient_capped_false_without_banner(monkeypatch):
    def fake(cmd, **k):
        if "has-session" in cmd:
            return _R(0)
        return _R(0, "a normal idle pane\n❯ \n  ? for shortcuts\n")
    monkeypatch.setattr(subprocess, "run", fake)
    assert s._recipient_capped("irsyad") is False


def test_recipient_capped_fails_toward_action_on_error(monkeypatch):
    # tmux blows up -> return False so the normal SLA action proceeds (never hide a real stall).
    def boom(cmd, **k):
        raise RuntimeError("tmux gone")
    monkeypatch.setattr(subprocess, "run", boom)
    assert s._recipient_capped("irsyad") is False


def test_recipient_capped_false_for_missing_or_empty_session(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _R(1))  # has-session returns non-zero
    assert s._recipient_capped("nope") is False
    assert s._recipient_capped("") is False
