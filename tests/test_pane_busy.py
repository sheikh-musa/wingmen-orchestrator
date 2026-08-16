"""'esc to interrupt' anywhere in the pane is not the same as a body that is busy NOW.

WHY THIS EXISTS — a bug I shipped and cai hit within the hour. self_recycle's wait-for-idle loop
asked `capture-pane -p | grep -q "esc to interrupt"`. That greps the WHOLE visible pane, scrollback
included, so a body that had been busy at any point still in the buffer reads as busy FOREVER. cai
sat at 838k with an idle composer while the waiter it fired refused to fire, holding a fire-window
lock that also suppressed her wakes. My gate, my bug, and it failed in the direction that looks
safe: it never clears a working body, it just never clears anything.

The live footer is the only part of a pane that describes NOW. Everything above it describes the
past — the same lesson as "an alert describes the past; only observation describes the present",
one layer down. lane_nudge.sh already read it correctly (last non-empty lines, and 'esc to
interrupt' present while the idle hint is absent); the loop I wrote did not reuse it.
"""
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "pane_busy.sh"

IDLE_FOOTER = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
BUSY_FOOTER = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ctrl+t\n"
OLD_BUSY_HISTORY = (
    "● Ran a long task\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ctrl+t\n"
    "✻ Churned for 45s\n"
    "────────────────────────────\n"
    "❯ \n"
    "────────────────────────────\n"
)


def _busy(pane_text: str) -> bool:
    r = subprocess.run(["bash", "-c", f'. "{_LIB}"; pane_busy_from_text'],
                       input=pane_text, capture_output=True, text=True)
    return r.returncode == 0


def test_a_live_busy_footer_reads_busy():
    assert _busy("some output\n" + BUSY_FOOTER) is True


def test_an_idle_footer_reads_idle():
    assert _busy("some output\n" + IDLE_FOOTER) is False


def test_a_stale_busy_marker_in_scrollback_does_not_read_as_busy():
    """The exact shape that stranded cai: the body finished, its old busy footer is still in
    the buffer, and the live footer says idle."""
    assert _busy(OLD_BUSY_HISTORY + IDLE_FOOTER) is False


def test_an_empty_capture_reads_busy_not_idle():
    """Fail CLOSED: if we cannot see the pane we must not conclude it is safe to clear."""
    assert _busy("") is True


# --------------------------------------------------------------------------------------
# the same defect, four more times, on the paths that DELIVER work to lanes
# --------------------------------------------------------------------------------------
#
# After fixing self_recycle I went looking for the pattern and found it in the wake path
# itself: agent_wake._pane_busy grepped the whole capture, so a lane whose scrollback held an
# old busy footer looked mid-turn to the waker FOREVER and its wake was debounced away. That
# is a silent delivery failure on the mechanism the whole fleet uses to reach a lane — and it
# looks exactly like "the lane is busy", which is why nobody chased it. Same shape in
# ingest.pane_working (whose own docstring says "footer"), lane_watchdog's orch-idle check,
# and lane_wedge_watchdog's hub nudge.
#
# The direction to fail on an UNREADABLE pane is NOT the same everywhere, so the helper makes
# each caller state it: clearing a body fails closed (unreadable = busy = do not touch);
# DELIVERING to one fails toward delivery (unreadable = idle = nudge), because a dropped nudge
# is recoverable and an undelivered one is invisible.

from scripts.lib import pane_busy as pb  # noqa: E402


def test_python_helper_ignores_a_stale_busy_marker_in_scrollback():
    assert pb.is_busy_text(OLD_BUSY_HISTORY + IDLE_FOOTER) is False


def test_python_helper_sees_a_live_busy_footer():
    assert pb.is_busy_text("output\n" + BUSY_FOOTER) is True


def test_unreadable_pane_direction_is_the_callers_choice():
    assert pb.is_busy_text("", on_unreadable=True) is True     # about to clear -> do not touch
    assert pb.is_busy_text("", on_unreadable=False) is False   # about to deliver -> nudge


def test_the_wake_path_no_longer_greps_the_whole_pane():
    src = (Path(__file__).resolve().parent.parent / "nervous_system" / "agent_wake.py").read_text()
    assert "pane_busy.is_busy" in src, (
        "agent_wake._pane_busy grepping the whole capture makes a lane with an old busy footer "
        "in its scrollback permanently unwakeable — a silent delivery failure that looks like a "
        "busy lane. It must delegate to the shared footer-scoped helper, not re-implement it "
        "(the re-implementation is how four copies of this bug came to exist)."
    )


@pytest.mark.parametrize("rel", [
    "nervous_system/ingest.py",
    "nervous_system/lane_watchdog.py",
    "nervous_system/lane_wedge_watchdog.py",
])
def test_every_other_copy_of_this_bug_delegates_to_the_shared_helper(rel):
    src = (Path(__file__).resolve().parent.parent / rel).read_text()
    assert "pane_busy.is_busy" in src, (
        f"{rel} decides busy/idle from a capture and must use the shared footer-scoped helper"
    )
