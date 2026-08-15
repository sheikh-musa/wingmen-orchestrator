"""Tests for switch_lane_token's post-relaunch verify helpers (composer_capture.sh).

WHY (2026-08-16, bus 22565/22582, #34): a re-token relaunch hits CC's folder-TRUST
prompt ("Is this a project you trust?") BEFORE --resume takes effect. `pane_up_healthy`
matched the bare '❯' glyph — but the trust menu's option marker IS '❯ 1. Yes, I trust
this folder' — so a lane PARKED at the trust prompt read as HEALTHY and switch_lane_token
reported PASS on 5 of 6 swept lanes that were actually dead. A PASS must NOT pass on a
boot/trust/resume prompt. These lock: (1) trust_prompt_present detects it; (2)
pane_up_healthy FAILS on it (so the switch reports a loud FAIL, not a silent parked lane).
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib" / "composer_capture.sh"

TRUST_PANE = """\
 Is this a project you trust?

 /Users/sheikhmusa/wingmen/projects/caai-lane

 ❯ 1. Yes, I trust this folder
   2. No, exit

 Enter to confirm · Esc to cancel
"""

RESUMED_PANE = """\
  ⎿  Read qa-tabung-fixtures.mjs (173 lines)
────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
"""


def _fn(fn: str, arg: str) -> int:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n{fn} "$1"', "_", arg],
    ).returncode


def test_trust_prompt_pane_is_detected():
    assert _fn("trust_prompt_present", TRUST_PANE) == 0       # detected
    assert _fn("trust_prompt_present", RESUMED_PANE) != 0     # not a false-positive


def test_trust_prompt_pane_is_not_healthy():
    # The load-bearing fix: a lane parked at the trust prompt must NOT read as healthy,
    # so switch_lane_token's PASS gate fails loudly instead of hiding a dead lane.
    assert _fn("pane_up_healthy", TRUST_PANE) != 0            # NOT healthy


def test_genuinely_resumed_pane_is_healthy():
    # Guard the other direction: a real resumed session must still pass.
    assert _fn("pane_up_healthy", RESUMED_PANE) == 0
