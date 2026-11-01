"""Wiring test for the #792 fix — the NUDGE path must consult attended_for().

cc-quality's residual on #792 (bus #88792): they reviewed the branch by reading it and
verified fail-posture on attended_for() itself, but nothing in the suite drives run()
far enough to prove the elif is REACHED and that it suppresses the nudge. Their words:
"F1 wiring-test (non-blocking)". This is that test.

WHY A WIRING TEST AND NOT A UNIT TEST: attended_for() and _attended_decision() are
already tested directly (test_sla_ask_offline_attended.py and friends). What was never
tested is the CALLSITE — that the cascade reaches the attended branch for a
non-page-eligible row, passes that row's real id, and is not shadowed by an earlier
continue. A unit test of the predicate cannot fail if the caller is deleted; this can.

Both directions are equally load-bearing, so both are asserted:
  - attended   -> SKIP nudge, no keystroke (the #792 fix)
  - unattended -> NUDGE still fires (a silencing change must fail toward action)

SCOPE: the PAGE-side attended gate (the #8723 fix this one mirrors) is deliberately not
re-tested here — it already has direct coverage in test_sla_read_parked_escalate.py and
test_sla_ask_offline_attended.py, and driving it through run() needs a conn surface deep
enough that the fixture would be testing the fixture. This file pins the NUDGE callsite,
which had none.
"""
import importlib

import pytest

w = importlib.import_module("scripts.priority_sla_watchdog")


class _FakeConn:
    """run() only needs close() on the dry path; every DB helper is stubbed below."""

    closed = False

    def close(self):
        self.closed = True


def _violation(mid, pri="P2", vt="unread", elapsed=65, agent="cc-hifz-1"):
    return {
        "agent": agent,
        "message_id": mid,
        "priority": pri,
        "from_agent": "orch-console",
        "subject": "an ask that was withdrawn",
        "violation_type": vt,
        "elapsed_minutes": elapsed,
        "threshold_minutes": 30,
    }


@pytest.fixture
def wired(monkeypatch):
    """Stub every DB/tmux touch point so run() can be driven in-process, dry."""
    monkeypatch.setattr(w, "_connect", lambda: _FakeConn())
    monkeypatch.setattr(w, "lane_map", lambda c: {})
    monkeypatch.setattr(w, "all_lane_map", lambda c: {})
    monkeypatch.setattr(w, "ensure_identity", lambda c: None)
    monkeypatch.setattr(w, "already_paged_on_bus", lambda c, m: False)
    monkeypatch.setattr(w, "audit", lambda *a, **k: None)


def _drive(monkeypatch, attended, violations):
    """Run one dry cycle; return (printed report, [(mid, vtype, agent), ...] asked)."""
    calls = []

    def fake_attended_for(conn, mid, vtype, agent):
        calls.append((mid, vtype, agent))
        return attended

    monkeypatch.setattr(w, "attended_for", fake_attended_for)
    w.run(dry=True, injected=violations, persist=True)
    return calls


# ── the fix: an attended, NON-page-eligible row skips the nudge ──────────────
# P2 is deliberately not page-eligible (ESCALATE_PRIORITIES = P0/P1), so these can
# only pass by reaching the elif — they cannot be satisfied by the older page gate.

def test_attended_p2_row_skips_the_nudge(wired, monkeypatch, capsys):
    _drive(monkeypatch, True, [_violation(88625, pri="P2")])
    text = capsys.readouterr().out
    assert "SKIP nudge #88625" in text
    assert "attended" in text
    assert "NUDGE #88625" not in text, "an attended row must not receive keystrokes"


def test_unattended_p2_row_still_nudges(wired, monkeypatch, capsys):
    """Fail-toward-action: the suppression must not be true-for-everything."""
    _drive(monkeypatch, False, [_violation(88625, pri="P2")])
    text = capsys.readouterr().out
    assert "NUDGE #88625" in text
    assert "SKIP nudge #88625" not in text


# ── the callsite contract: right id, right violation type, right agent ──────

def test_attended_for_is_asked_about_this_rows_own_id(wired, monkeypatch, capsys):
    """A future edit passing the state KEY or the subject instead of message_id would
    silently ask about the wrong row and suppress the wrong nudge."""
    calls = _drive(monkeypatch, True, [_violation(88645, vt="unread")])
    capsys.readouterr()
    assert calls == [(88645, "unread", "cc-hifz-1")]


# ── one real shape, end to end: the withdrawn pair of 2026-11-01 ────────────

def test_both_withdrawn_hifz_rows_are_skipped_in_one_pass(wired, monkeypatch, capsys):
    """The live incident: two rows console withdrew with responded_at set and read_at
    NULL. Neither is page-eligible; both were re-nudged every 90s cycle."""
    monkeypatch.setattr(w, "attended_for", lambda c, m, v, a: m in (88625, 88645))
    w.run(dry=True, injected=[_violation(88625), _violation(88645)], persist=True)
    text = capsys.readouterr().out
    assert "SKIP nudge #88625" in text and "SKIP nudge #88645" in text
    assert "NUDGE #" not in text, "no keystroke may be produced for either row"
