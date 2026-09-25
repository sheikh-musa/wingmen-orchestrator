"""Autocompact pilot-marker resolution (Nazim #43192 root fix).

The bug: launch_dangerous_cc.sh resolved the pilot session with an UNTARGETED
`tmux display-message -p '#S'`. In a DETACHED launch (`tmux new-session -d ... "launch..."`)
there is no current tmux client, so it returned EMPTY and the per-session
`.<session>_autocompact_pct` marker was SILENTLY skipped — substrate pilot run-2 never got
its override (a pilot knob that silently fails = "monitor manufacturing false confidence").

Fix (this lib): resolve the session via the pane's own target ($TMUX_PANE), fall back to an
explicit $LANE_SESSION env, then the untargeted form; and if the session can't be resolved
while a per-session marker EXISTS, warn LOUD instead of skipping silently."""
import os
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "autocompact_override.sh"


def _fake_tmux(dir_):
    """A tmux stand-in so resolution is HERMETIC (never the host's live tmux server).
    `display-message ... '#S'` echoes $FAKE_TMUX_SESSION (default empty = unresolvable)."""
    t = dir_ / "tmux"
    t.write_text('#!/usr/bin/env bash\n'
                 'if [ "$1" = display-message ]; then printf "%s" "${FAKE_TMUX_SESSION:-}"; fi\n'
                 'exit 0\n')
    t.chmod(0o755)
    return dir_


def _run(orch_dir, env_extra):
    bindir = _fake_tmux(Path(orch_dir))  # put the fake tmux beside the markers, first on PATH
    env = dict(os.environ, **{k: str(v) for k, v in env_extra.items()})
    env.pop("TMUX", None)
    env["PATH"] = f"{bindir}:{os.environ['PATH']}"
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\nresolve_autocompact_override "{orch_dir}"'],
        capture_output=True, text=True, env=env)


def test_resolves_per_session_marker_via_LANE_SESSION(tmp_path):
    # the DETACHED-launch case: no tmux client, but LANE_SESSION names the lane -> marker read
    (tmp_path / ".substrate-cleanup_autocompact_pct").write_text("20\n")
    r = _run(tmp_path, {"LANE_SESSION": "substrate-cleanup", "TMUX_PANE": ""})
    assert r.stdout.split()[0] == "20", f"detached launch must read the marker; got {r.stdout!r} {r.stderr!r}"
    assert ".substrate-cleanup_autocompact_pct" in r.stdout


def test_resolves_via_TMUX_PANE_targeted(tmp_path):
    # THE core fix: a detached launch has $TMUX_PANE set (inside the pane) even with no client;
    # the pane-targeted display-message resolves the session -> marker read.
    (tmp_path / ".substrate-cleanup_autocompact_pct").write_text("20\n")
    r = _run(tmp_path, {"TMUX_PANE": "%7", "FAKE_TMUX_SESSION": "substrate-cleanup", "LANE_SESSION": ""})
    assert r.stdout.split()[0] == "20", f"TMUX_PANE-targeted resolution must read the marker; {r.stdout!r} {r.stderr!r}"


def test_fleet_fallback_when_no_session_marker(tmp_path):
    (tmp_path / ".fleet_autocompact_pct").write_text("50\n")
    r = _run(tmp_path, {"LANE_SESSION": "somelane", "TMUX_PANE": ""})
    assert r.stdout.split()[0] == "50"
    assert ".fleet_autocompact_pct" in r.stdout


def test_per_session_marker_wins_over_fleet(tmp_path):
    (tmp_path / ".foo_autocompact_pct").write_text("20\n")
    (tmp_path / ".fleet_autocompact_pct").write_text("50\n")
    r = _run(tmp_path, {"LANE_SESSION": "foo", "TMUX_PANE": ""})
    assert r.stdout.split()[0] == "20"


def test_loud_warn_when_session_unresolvable_but_marker_exists(tmp_path):
    # session cannot be resolved (no TMUX_PANE, no LANE_SESSION, no client) but a per-session
    # marker is present -> must WARN LOUD, not silently skip (the #43192 failure class)
    (tmp_path / ".substrate-cleanup_autocompact_pct").write_text("20\n")
    r = _run(tmp_path, {"LANE_SESSION": "", "TMUX_PANE": ""})
    assert r.stdout.strip() == "", "must not emit an override when the session is unresolvable"
    assert "could NOT resolve" in r.stderr and "SKIPPED" in r.stderr, \
        f"a skipped per-session pilot marker must warn LOUD; stderr={r.stderr!r}"


def test_silent_when_no_marker_at_all(tmp_path):
    # no markers anywhere -> no override, no warning (default-off is byte-identical to today)
    r = _run(tmp_path, {"LANE_SESSION": "", "TMUX_PANE": ""})
    assert r.stdout.strip() == ""
    assert "could NOT resolve" not in r.stderr


def test_no_untargeted_leak_when_outside_tmux(tmp_path):
    # Nazim #43192 review add: OUTSIDE any tmux client (TMUX unset), the untargeted
    # `display-message -p '#S'` returns the server's CURRENT session (e.g. another lane), so the
    # old step-3 would silently read a DIFFERENT lane's marker. Guarded behind $TMUX, step 3 is
    # skipped when outside a client: no override is emitted, and the skipped per-session marker
    # warns LOUD. (Here the fake tmux WOULD resolve "otherlane" if step 3 ran — it must not.)
    (tmp_path / ".otherlane_autocompact_pct").write_text("20\n")  # a DIFFERENT lane's marker
    r = _run(tmp_path, {"LANE_SESSION": "", "TMUX_PANE": "", "FAKE_TMUX_SESSION": "otherlane"})
    assert r.stdout.strip() == "", \
        f"outside tmux must not leak another session's marker; got {r.stdout!r}"
    assert "could NOT resolve" in r.stderr and "SKIPPED" in r.stderr, \
        f"a skipped per-session marker must warn LOUD; stderr={r.stderr!r}"
