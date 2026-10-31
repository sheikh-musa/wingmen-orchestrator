"""boot_fleet_health.sh's _self_kick must use the fleet's VERIFIED submit path.

A bare `tmux send-keys ... Enter` frequently fails to submit: the prompt is
typed into the composer but never sent, so the freshly restarted SRE sits IDLE
until a human presses Enter by hand. That is a dead-man's-switch failure on a
P0 recovery path — the restart looks successful and the body is silently doing
nothing (orch-console #87340; cost 4 min on the 2026-10-31 17:36Z restart).

lane_nudge.sh exists precisely to replace that unreliable submit (cai
CAI-RESP-284 HARD RULE 1). _self_kick must go through it, must NOT hold a
fire window of its own (lane_nudge CHECKS the same window and would self-refuse
for the whole hold), and must report the outcome LOUDLY rather than assume.
"""
import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "boot_fleet_health.sh"


def _self_kick_body() -> str:
    """The function's EXECUTABLE lines. Comments are stripped first: the fix's own
    explanation necessarily quotes the removed patterns ("a bare send-keys Enter",
    "do NOT re-add a fire_window_hold"), and a test that greps prose would flag its
    own documentation instead of the code."""
    text = _SCRIPT.read_text(encoding="utf-8")
    start = text.index("_self_kick() {")
    end = text.index("\n}\n", start) + 2
    code_lines = [
        ln for ln in text[start:end].splitlines()
        if not ln.lstrip().startswith("#")
    ]
    body = "\n".join(code_lines)
    # Guard the extractor itself: if the marker or slicing ever drifts, fail here
    # rather than silently asserting against an empty/whole-file string.
    assert body.lstrip().startswith("_self_kick() {"), "function extraction drifted"
    return body


def test_self_kick_delegates_to_lane_nudge():
    assert "lane_nudge.sh" in _self_kick_body(), (
        "_self_kick must submit through lane_nudge.sh, the fleet's verified-submit path"
    )


def test_self_kick_has_no_bare_send_keys_enter():
    body = _self_kick_body()
    offenders = re.findall(r"send-keys[^\n]*\bEnter\b", body)
    assert not offenders, (
        "bare send-keys Enter is the unreliable submit this fix removes: %r" % offenders
    )
    assert not re.findall(r"send-keys[^\n]*\s-l\s", body), "must not type the kick by hand"


def test_self_kick_does_not_hold_a_fire_window():
    # lane_nudge.sh refuses (exit 4) while a fire window is held, so a self-held
    # window would make _self_kick refuse itself for its entire duration.
    assert "fire_window_hold" not in _self_kick_body(), (
        "_self_kick must not hold a fire window it then asks lane_nudge to enter"
    )


def test_self_kick_reports_a_failed_submit_loudly():
    body = _self_kick_body()
    assert "kick_rc" in body, "the submit's exit status must be captured, not assumed"
    assert "NOT VERIFIED" in body, (
        "a failed submit must be logged LOUD — a silent no-op fakes a successful boot"
    )
