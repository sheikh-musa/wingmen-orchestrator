"""self_recycle must survive a body that keeps being woken.

WHY THIS EXISTS. cai fired self_recycle.sh at 2026-08-16 ~12:5xZ. The detach worked and the
process survived, but when it fired at DELAY the body was mid-turn — a `[wake] new inbox item`
nudge had arrived inside the 60s window — and reset_cai's BUSY gate correctly refused. The tool
was not broken and the gate was not wrong; the SHAPE was. A one-shot "fire once at T+60" assumes
the body will be idle at exactly T+60, and a body on a live bus is woken precisely because it is
the kind of body worth recycling.

Two fixes, and they compose:
  * TAKE THE FIRE-WINDOW HOLD FOR THE WHOLE WAIT, not just inside the reset. The hold stops the
    nudgers typing, so nothing NEW can start a turn while we wait for the current one to end.
    Held early it prevents the collision; held only inside the reset (as reset_*.sh does) it is
    already too late — the busy gate has run by then.
  * WAIT FOR IDLE rather than firing blind. Poll until the pane is idle, then fire, with a bounded
    timeout so a genuinely stuck body fails loudly instead of hanging forever.

The alternative — "recycle me externally from an idle moment" — puts the operator's thumb back on
the button, which is the thing this whole line of work exists to remove.
"""
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "self_recycle.sh"
_SRC = _SCRIPT.read_text()


@pytest.fixture()
def handoff(tmp_path):
    """A FRESH, real-sized restore point — the freshness gate is measured against the clock,
    so a fixture file is the honest way to exercise everything downstream of it."""
    p = tmp_path / "handoff-NOW.md"
    p.write_text("# fresh handoff\n" + ("state line\n" * 200))
    return str(p)


def _run(*args, **kw):
    return subprocess.run(["bash", str(_SCRIPT), *args], capture_output=True, text=True,
                          cwd=str(_ROOT), **kw)


def test_dry_run_reports_the_session_it_would_quiesce_and_wait_on(handoff):
    """The caller must be able to SEE which pane this will act on before it fires — a
    self_recycle aimed at the wrong session is a body cleared that never asked."""
    out = _run("--reset", "scripts/reset_cai.sh",
               "--handoff", handoff, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert re.search(r"session[: ]+cai", out.stdout + out.stderr, re.I), (
        "dry-run must name the target session (derived from the reset script or --session)"
    )


def test_explicit_session_flag_overrides_the_derivation(handoff):
    out = _run("--reset", "scripts/reset_lane.sh", "--session", "irsyad-prog2",
               "--handoff", handoff, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "irsyad-prog2" in out.stdout + out.stderr


def test_unknown_session_is_refused_rather_than_guessed(handoff):
    """reset_lane.sh takes its target as an argument, so there is nothing to derive. Guessing
    a session name here would aim a /clear at whatever happened to match."""
    out = _run("--reset", "scripts/reset_lane.sh",
               "--handoff", handoff, "--dry-run")
    assert out.returncode != 0
    assert "session" in (out.stderr + out.stdout).lower()


def test_it_holds_the_fire_window_before_waiting_not_only_inside_the_reset():
    assert "fire_window" in _SRC, (
        "self_recycle must take the fire-window hold itself. reset_*.sh takes its hold AFTER the "
        "busy gate, which is too late: a wake that lands during the delay starts a turn and the "
        "gate then refuses — exactly what happened to cai."
    )
    hold_at = _SRC.index("fire_window")
    fire_at = _SRC.index("env -u TMUX_PANE bash")
    assert hold_at < fire_at, "the hold must be taken before the reset is invoked, not after"


def test_it_waits_for_idle_instead_of_firing_once_and_hoping():
    assert re.search(r"esc to interrupt", _SRC), (
        "the scheduled job must re-check the pane for the busy marker and wait, rather than "
        "firing blind at DELAY"
    )
    assert re.search(r"MAX_WAIT|max-wait", _SRC), (
        "the wait must be bounded — a genuinely stuck body should fail loudly, not hang"
    )
