"""agent_wake pending-retry markers (Fable audit 2026-09-30 B-1 (iii), op#23531 PR A).

A realtime doorbell outcome of busy / debounced / submit-unverified / wake-cap used to be a
SINGLE dropped attempt (263 'busy' in 48h on the Mini); only the backstop sweep retried, and
by then the row was age-capped. Now wake_agent() records a per-row pending marker on those
outcomes (and clears it on a verified wake) so the sweep retries the row BEFORE its grace.
State lives under scripts/.agent_wake/pending/ — the same host-local dir as the cap state."""
import json
import os
import sys

import pytest

NS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nervous_system")
sys.path.insert(0, NS)
import agent_wake  # noqa: E402


@pytest.fixture
def wake_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_wake, "_WAKE_DIR", tmp_path)
    monkeypatch.setattr(agent_wake, "_PENDING_DIR", tmp_path / "pending")
    return tmp_path


def test_record_and_list_and_clear_pending(wake_dir):
    agent_wake.record_pending("cc-quality", 42, "busy (mid-turn)")
    agent_wake.record_pending("cc-quality", 42, "busy (mid-turn)")     # idempotent
    agent_wake.record_pending("cc-oeh", 43, "submit-unverified")
    rows = agent_wake.pending_rows()
    assert sorted((r["row_id"], r["agent"]) for r in rows) == [(42, "cc-quality"), (43, "cc-oeh")]
    assert all("ts" in r and "why" in r for r in rows)
    agent_wake.clear_pending(42)
    assert [r["row_id"] for r in agent_wake.pending_rows()] == [43]
    agent_wake.clear_pending(42)                                        # already gone: no error


def test_pending_ids_are_filename_safe(wake_dir):
    agent_wake.record_pending("cc-x", "../evil", "busy (mid-turn)")
    files = list((wake_dir / "pending").iterdir())
    assert len(files) == 1 and ".." not in files[0].name and "/" not in files[0].name


@pytest.mark.parametrize("why", sorted(agent_wake.PENDING_RETRY_WHYS))
def test_retryable_outcomes_are_the_transient_ones(why):
    assert why in {"busy (mid-turn)", "debounced", "submit-unverified", "wake-cap"}


def test_wake_agent_records_pending_on_busy_and_clears_on_verified_wake(wake_dir, monkeypatch):
    monkeypatch.setattr(agent_wake, "resolve_tmux_session", lambda a: "sess")
    monkeypatch.setattr(agent_wake, "_pane_busy", lambda s: True)
    res = agent_wake.wake_agent("cc-quality", "msg #7", row_id=7, now=1000.0)
    assert res["why"] == "busy (mid-turn)"
    assert [r["row_id"] for r in agent_wake.pending_rows()] == [7]
    # pane frees up, submit verifies → marker cleared
    monkeypatch.setattr(agent_wake, "_pane_busy", lambda s: False)
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 0)
    res = agent_wake.wake_agent("cc-quality", "msg #7", row_id=7, now=1100.0)
    assert res["woke"] is True
    assert agent_wake.pending_rows() == []


def test_wake_agent_records_pending_on_debounce_and_rc3(wake_dir, monkeypatch):
    monkeypatch.setattr(agent_wake, "resolve_tmux_session", lambda a: "sess")
    monkeypatch.setattr(agent_wake, "_pane_busy", lambda s: False)
    monkeypatch.setattr(agent_wake, "_verified_submit", lambda s, sig, row_id=None: 3)
    res = agent_wake.wake_agent("cc-quality", "msg #8", row_id=8, now=1000.0)
    assert res["why"] == "submit-unverified"
    assert [r["row_id"] for r in agent_wake.pending_rows()] == [8]
    # a second wake within the debounce window is 'debounced' → still pending
    res = agent_wake.wake_agent("cc-quality", "msg #9", row_id=9, now=1010.0)
    assert res["why"] == "debounced"
    assert sorted(r["row_id"] for r in agent_wake.pending_rows()) == [8, 9]


def test_wake_agent_does_not_record_pending_for_terminal_outcomes(wake_dir, monkeypatch):
    monkeypatch.setattr(agent_wake, "resolve_tmux_session", lambda a: None)
    res = agent_wake.wake_agent("cc-quality", "msg #10", row_id=10, now=1000.0)
    assert res["why"] == "no live session" and agent_wake.pending_rows() == []
    # no row_id → nothing to mark (command nudges)
    monkeypatch.setattr(agent_wake, "resolve_tmux_session", lambda a: "sess")
    monkeypatch.setattr(agent_wake, "_pane_busy", lambda s: True)
    agent_wake.wake_agent("cc-quality", "cmd", now=1000.0)
    assert agent_wake.pending_rows() == []
