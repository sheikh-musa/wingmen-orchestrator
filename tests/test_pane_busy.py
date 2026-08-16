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
