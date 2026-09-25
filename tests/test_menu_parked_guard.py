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
# The SendFeedback drafts dialog: a modal that intercepts keystrokes (so a plain
# lane_nudge would type INTO it, e.g. hitting "send"). Its footer carries the
# "to dismiss" option that the benign review widget below never has (Nazim #43083).
# REAL capture (Nazim #43096 condition 1): last 8 lines of a fleet-health lane_nudge
# capture — own-lane, no client content. Note the footer is NOT the last line (an
# "Update installed" banner follows), which is why pane_is_menu scans tail -6, not -1.
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
SENDFEEDBACK_PANE = (_FIXTURES / "sendfeedback_pane.txt").read_text().rstrip("\n")
# The benign subagent-review widget — NOT a wedge, must stay wakeable (memory
# review-widget-is-not-a-wedge-signal). Same review/send footer but NO "to dismiss".
# NEGATIVE control (no real capture existed in the own-lane corpus): the discriminator
# under test is purely the ABSENCE of "to dismiss", so a minimal representation suffices.
REVIEW_WIDGET_PANE = "\n".join([
    "  ● Reviewing proposed changes",
    "  ❯ ",
    "  1 to review · 2 to send",
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


def test_pane_is_menu_detects_a_sendfeedback_dialog(tmp_path):
    # The SendFeedback modal intercepts keys; a plain nudge would type INTO it. Its
    # "to dismiss" footer must be classified as a menu so lane_nudge refuses (Nazim #43083).
    tmux = _fake_tmux(tmp_path, SENDFEEDBACK_PANE)
    assert _pane_is_menu(tmux, SENDFEEDBACK_PANE) is True


def test_pane_is_menu_does_NOT_fire_on_the_benign_review_widget(tmp_path):
    # Specificity guard (memory review-widget-is-not-a-wedge-signal): the subagent-review
    # widget ("N to review · N to send", NO "dismiss") is wakeable and must NOT be refused.
    tmux = _fake_tmux(tmp_path, REVIEW_WIDGET_PANE)
    assert _pane_is_menu(tmux, REVIEW_WIDGET_PANE) is False


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


def _run_lane_nudge(tmp_path, capture_text, env_extra=None):
    _fake_tmux(tmp_path, capture_text)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    r = subprocess.run(["bash", str(LANE_NUDGE), "somesess", "wake up and drain your inbox"],
                       capture_output=True, text=True, env=env, timeout=60)
    log = tmp_path / "sendkeys.log"
    sends = log.read_text() if log.exists() else ""
    return r, sends


def test_lane_nudge_REFUSES_a_row_at_its_delivery_ceiling_with_ZERO_sendkeys(tmp_path):
    """Per-row ceiling (Nazim #43063): a row already delivered ROW_CAP times is refused
    (exit 7) and never re-typed — bounds ANY waker at the common choke."""
    rcdir = tmp_path / "rc"; rcdir.mkdir()
    (rcdir / "ROW1").write_text(f"{int(__import__('time').time())}\n")  # 1 recent delivery
    r, sends = _run_lane_nudge(tmp_path, IDLE_PANE, env_extra={
        "LANE_NUDGE_ROW_ID": "ROW1", "ROW_CEILING_DIR": str(rcdir),
        "ROW_CAP": 1})
    assert r.returncode == 7, f"expected exit 7 (row-cap), got {r.returncode}: {r.stderr}"
    assert sends == "", f"row at ceiling must not be re-typed:\n{sends}"


def test_lane_nudge_FAILS_CLOSED_on_unwritable_ceiling_state(tmp_path):
    """Fail-closed (Nazim #43114): if the per-row ceiling STATE can't be created/read,
    lane_nudge REFUSES (exit 8) rather than deliver uncapped — an unbounded loop is the bug."""
    afile = tmp_path / "afile"; afile.write_text("x")  # a FILE where the dir must be
    r, sends = _run_lane_nudge(tmp_path, IDLE_PANE, env_extra={
        "LANE_NUDGE_ROW_ID": "ROWX", "ROW_CEILING_DIR": str(afile / "sub"), "ROW_CAP": 5})
    assert r.returncode == 8, f"expected exit 8 (fail-closed), got {r.returncode}: {r.stderr}"
    assert sends == "", "must not type when the ceiling state is unavailable"


def test_lane_nudge_under_row_ceiling_delivers_and_records(tmp_path):
    """A row under the ceiling still delivers, and a successful delivery is RECORDED so
    repeated wakes eventually hit the cap. (Idle pane won't verify 'working', so exit!=7
    and send-keys DID happen — delivery attempted, i.e. not blocked by the ceiling.)"""
    rcdir = tmp_path / "rc"
    r, sends = _run_lane_nudge(tmp_path, IDLE_PANE, env_extra={
        "LANE_NUDGE_ROW_ID": "ROW2", "ROW_CEILING_DIR": str(rcdir),
        "ROW_CAP": 5})
    assert r.returncode != 7, "an under-cap row must not be ceiling-refused"
    assert sends != "", "an under-cap row must still be typed"


def test_lane_nudge_REFUSES_a_menu_pane_with_ZERO_sendkeys(tmp_path):
    """THE invariant: a menu pane -> lane_nudge refuses (exit 5) and never send-keys."""
    r, sends = _run_lane_nudge(tmp_path, MENU_PANE)
    assert r.returncode == 5, f"expected exit 5 (menu-refused), got {r.returncode}: {r.stderr}"
    assert sends == "", f"AUTHORIZATION SLIP: send-keys reached a menu pane:\n{sends}"
    assert "MENU" in r.stderr.upper()


def test_lane_nudge_REFUSES_a_sendfeedback_dialog_with_ZERO_sendkeys(tmp_path):
    """A SendFeedback dialog must be refused like any menu — never type into it (which
    could hit 'send'). Recovery is a deliberate manual 'dismiss', not an auto-nudge."""
    r, sends = _run_lane_nudge(tmp_path, SENDFEEDBACK_PANE)
    assert r.returncode == 5, f"expected exit 5 (menu-refused), got {r.returncode}: {r.stderr}"
    assert sends == "", f"AUTHORIZATION SLIP: send-keys reached a SendFeedback dialog:\n{sends}"
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
