"""Menu-parked authorization-slip guard (Nazim #40469).

A pane parked in an interactive selection MENU (AskUserQuestion / permission / trust
dialog) intercepts keystrokes, so ANY send-keys into it moves/commits the selection =
answering an authorization prompt on the lane's behalf. The invariant: NO caller may
send-keys into such a pane. Enforced at the SINGLE send-keys choke point (lane_nudge.sh,
which the armed wedge-watchdog nudge tier + the SLA watchdog + the wake path all funnel
through) using the ONE fleet menu definition (composer_capture.sh pane_is_menu).

These tests use a FAKE tmux on PATH: capture-pane returns a fixture; send-keys is
recorded. The core assertion is ZERO send-keys on a menu pane."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "composer_capture.sh"
LANE_NUDGE = REPO / "scripts" / "lane_nudge.sh"

MENU_PANE = "\n".join([
    "  Claude needs your permission to run this tool",
    "  ❯ 1. Yes",
    "    2. No, and tell Claude what to do differently",
    "  ↑/↓ to navigate · enter to select · esc to cancel",
])
IDLE_PANE = "\n".join([
    "  earlier transcript line",
    "  ─────────────────────────",
    "  ❯ ",
    "  ─────────────────────────",
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
])


def _fake_tmux(dir_: Path, capture_text: str) -> Path:
    """A tmux stand-in: has-session ok, capture-pane -> fixture, send-keys -> logged."""
    fixture = dir_ / "pane.txt"
    fixture.write_text(capture_text)
    log = dir_ / "sendkeys.log"
    tmux = dir_ / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        "  has-session) exit 0 ;;\n"
        f"  capture-pane) cat {fixture} ;;\n"
        f"  send-keys) printf '%s\\n' \"$*\" >> {log} ;;\n"
        "  *) : ;;\n"
        "esac\n"
        "exit 0\n")
    tmux.chmod(0o755)
    return tmux


def _pane_is_menu(tmux_bin: Path, capture_text: str) -> bool:
    # Drive the shared predicate exactly as callers do: pane_is_menu <tmux-bin> <pane>.
    snippet = f'. "{LIB}"\npane_is_menu "{tmux_bin}" x\necho rc=$?'
    r = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] == "rc=0"


def test_pane_is_menu_detects_a_selection_menu(tmp_path):
    tmux = _fake_tmux(tmp_path, MENU_PANE)
    assert _pane_is_menu(tmux, MENU_PANE) is True


def test_pane_is_menu_does_not_fire_on_an_idle_pane(tmp_path):
    tmux = _fake_tmux(tmp_path, IDLE_PANE)
    assert _pane_is_menu(tmux, IDLE_PANE) is False


def _pane_is_menu_rc(tmux_bin: Path) -> int:
    snippet = f'. "{LIB}"\npane_is_menu "{tmux_bin}" x\necho rc=$?'
    r = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
    line = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("rc=")][-1]
    return int(line.split("=")[1])


def test_pane_is_menu_returns_2_on_unreadable_pane(tmp_path):
    # Empty/failed capture -> UNREADABLE (rc 2), distinct from readable-not-a-menu (rc 1).
    tmux = _fake_tmux(tmp_path, "")
    assert _pane_is_menu_rc(tmux) == 2


def test_pane_is_menu_returns_1_on_readable_non_menu(tmp_path):
    tmux = _fake_tmux(tmp_path, IDLE_PANE)
    assert _pane_is_menu_rc(tmux) == 1


def _run_lane_nudge(tmp_path, capture_text):
    _fake_tmux(tmp_path, capture_text)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    r = subprocess.run(["bash", str(LANE_NUDGE), "somesess", "wake up and drain your inbox"],
                       capture_output=True, text=True, env=env, timeout=60)
    log = tmp_path / "sendkeys.log"
    sends = log.read_text() if log.exists() else ""
    return r, sends


def test_lane_nudge_REFUSES_a_menu_pane_with_ZERO_sendkeys(tmp_path):
    """THE invariant: a menu pane -> lane_nudge refuses (exit 5) and never send-keys."""
    r, sends = _run_lane_nudge(tmp_path, MENU_PANE)
    assert r.returncode == 5, f"expected exit 5 (menu-refused), got {r.returncode}: {r.stderr}"
    assert sends == "", f"AUTHORIZATION SLIP: send-keys reached a menu pane:\n{sends}"
    assert "MENU" in r.stderr.upper()


def test_lane_nudge_does_send_keys_on_a_non_menu_pane(tmp_path):
    """Specificity: the guard must NOT over-refuse — an idle (non-menu) pane still gets
    typed into (so real nudges are not silently swallowed). We only assert send-keys DID
    happen and the exit is not the menu-refusal code."""
    r, sends = _run_lane_nudge(tmp_path, IDLE_PANE)
    assert r.returncode != 5, "idle pane must not hit the menu-refusal path"
    assert sends != "", "a non-menu pane must still receive the nudge send-keys"


def test_lane_nudge_REFUSES_an_unreadable_pane_with_ZERO_sendkeys(tmp_path):
    """A guard must not type BLIND (Nazim #40507): an unreadable pane (empty capture) may
    itself be a menu, so lane_nudge refuses (exit 6) and never send-keys."""
    r, sends = _run_lane_nudge(tmp_path, "")
    assert r.returncode == 6, f"expected exit 6 (unreadable-refused), got {r.returncode}: {r.stderr}"
    assert sends == "", f"typed blind into an unreadable pane:\n{sends}"
    assert "read" in r.stderr.lower() and "blind" in r.stderr.lower()
