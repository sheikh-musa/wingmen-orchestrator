"""Hardened re-token verify (op#12030 f/u / op#12043): switch_lane_token must not
call a re-token "PASS" on the auth_fp flip ALONE.

Failure it closes: `--resume` on a large/old claude session opens CC's
"Resume from summary / full / don't-ask" menu, and the relaunched lane PARKS there
until answered. The auth_fp is already stamped (process is up on the new account),
so an fp-only verify reports PASS while the lane is soft-wedged at the menu — and a
blind fleet-wide flip-all would park EVERY big lane there at once. So the tool must
(1) auto-answer the resume menu (DEFAULT full/non-lossy; --summary opt-in) and
(2) extend the verify to a HEALTHY pane (composer prompt or actively working, and
NOT the menu), not just the fp.

Layers (mirrors tests/test_reset_busy_gate.py):
  * UNIT: the pure pane predicates in composer_capture.sh (resume_menu_present /
    pane_up_healthy / resume_menu_keys), sourced + called on fixture pane text.
  * STATIC: switch_lane_token.sh wires them — auto-answers the menu, gates RESULT
    on a healthy pane, and accepts --summary (default full).
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib" / "composer_capture.sh"
SCRIPT = REPO / "scripts" / "switch_lane_token.sh"

# ── fixture panes ────────────────────────────────────────────────────────────
MENU = (
    "  This session is 1d 17h old and 244.6k tokens.\n"
    "  Resuming the full session will consume a substantial portion of your usage\n"
    "  We recommend resuming from a summary.\n"
    "  ❯ 1. Resume from summary (recommended)\n"
    "    2. Resume full session as-is\n"
    "    3. Don't ask me again\n"
    "  Enter to confirm · Esc to cancel"
)
PROMPT = (
    "  some prior transcript line\n"
    "────────\n"
    "❯ \n"
    "────────\n"
    "  bypass permissions on"
)
BUSY = "  ✦ Crunched for 1m 50s\n  ... esc to interrupt"
BLANK = ""


def _present(text) -> bool:
    return subprocess.run(["bash", "-c", f'source "{LIB}"; resume_menu_present "$1"', "_", text]).returncode == 0


def _healthy(text) -> bool:
    return subprocess.run(["bash", "-c", f'source "{LIB}"; pane_up_healthy "$1"', "_", text]).returncode == 0


def test_resume_menu_present_matrix():
    assert _present(MENU) is True
    assert _present(PROMPT) is False
    assert _present(BUSY) is False
    assert _present(BLANK) is False


# ── UNIT: pane_up_healthy ────────────────────────────────────────────────────
def test_pane_up_healthy_matrix():
    assert _healthy(PROMPT) is True, "idle composer prompt = healthy"
    assert _healthy(BUSY) is True, "actively working = healthy"
    assert _healthy(MENU) is False, "parked at resume menu = NOT healthy"
    assert _healthy(BLANK) is False, "blank/booting = not yet healthy"


# ── UNIT: resume_menu_keys (default full, --summary -> summary) ───────────────
def _keys(mode) -> str:
    return subprocess.run(["bash", "-c", f'source "{LIB}"; resume_menu_keys "$1"', "_", mode],
                          capture_output=True, text=True).stdout.strip()


def test_resume_menu_keys():
    assert _keys("full") == "Down Enter", "full = move to option 2 then confirm"
    assert _keys("summary") == "Enter", "summary = confirm the default-highlighted option 1"
    assert _keys("") == "Down Enter", "empty/unspecified defaults to full (non-lossy)"


# ── STATIC: switch_lane_token wires it ───────────────────────────────────────
def test_script_auto_answers_menu_and_gates_on_health():
    src = SCRIPT.read_text()
    assert "resume_menu_present" in src, "must detect the resume menu"
    assert "pane_up_healthy" in src, "RESULT must gate on a healthy pane, not just fp"
    assert "--summary" in src, "must accept --summary (default full)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS: {name}")
