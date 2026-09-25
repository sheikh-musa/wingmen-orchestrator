"""The SHARED tmux-session resolver (scripts/lib/lane_session.sh, #152 review) that the
launcher uses for the model cascade, the autocompact marker AND the subagent cascade.

The bug it fixes: an UNTARGETED `tmux display-message -p '#S'` returns EMPTY on a DETACHED
launch (no client), so every per-body `.<session>_*` marker was silently skipped; and OUTSIDE
a client it returns the SERVER's current session (a DIFFERENT lane). Resolution order:
  1. $TMUX_PANE-targeted   2. $LANE_SESSION   3. untargeted ONLY when $TMUX is set.
These tests drive the ACTUAL bash function (shipped path == tested path).
"""
import os
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "lane_session.sh"


def _fake_tmux(dir_):
    """A hermetic tmux stand-in: `display-message ... '#S'` echoes $FAKE_TMUX_SESSION
    (default empty = unresolvable). Never touches the host's live tmux server."""
    t = dir_ / "tmux"
    t.write_text('#!/usr/bin/env bash\n'
                 'if [ "$1" = display-message ]; then printf "%s" "${FAKE_TMUX_SESSION:-}"; fi\n'
                 'exit 0\n')
    t.chmod(0o755)
    return dir_


def _resolve(tmp_path, env_extra):
    bindir = _fake_tmux(tmp_path)
    env = dict(os.environ)
    for k in ("TMUX", "TMUX_PANE", "LANE_SESSION", "FAKE_TMUX_SESSION"):
        env.pop(k, None)  # clean base, then env_extra sets exactly what the case wants
    for k, v in env_extra.items():
        if v == "":
            env.pop(k, None)
        else:
            env[k] = str(v)
    env["PATH"] = f"{bindir}:{os.environ['PATH']}"
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\nresolve_lane_session', ], capture_output=True, text=True, env=env,
    ).stdout


def test_tmux_pane_targeted_wins(tmp_path):
    # a detached launch has $TMUX_PANE set (inside the pane) even with no client
    out = _resolve(tmp_path, {"TMUX_PANE": "%7", "FAKE_TMUX_SESSION": "scholar"})
    assert out == "scholar"


def test_lane_session_env_when_no_pane(tmp_path):
    # THE detached-launch case Nazim (c): no client, no TMUX_PANE, but LANE_SESSION names the
    # lane -> resolved, so the per-body .<session>_* pointer is NOT skipped.
    out = _resolve(tmp_path, {"LANE_SESSION": "scholar"})
    assert out == "scholar"


def test_untargeted_only_inside_tmux_client(tmp_path):
    # OUTSIDE a client (TMUX unset): the untargeted form is skipped even though the fake tmux
    # WOULD resolve "otherlane" -> returns empty (no cross-lane leak).
    out = _resolve(tmp_path, {"FAKE_TMUX_SESSION": "otherlane"})
    assert out == "", f"outside a client must not resolve the server's current session; got {out!r}"


def test_untargeted_used_inside_tmux_client(tmp_path):
    # INSIDE a client ($TMUX set): untargeted resolves normally.
    out = _resolve(tmp_path, {"TMUX": "/tmp/tmux-x,1,0", "FAKE_TMUX_SESSION": "scholar"})
    assert out == "scholar"


def test_unresolvable_returns_empty(tmp_path):
    # nothing set -> empty (callers that gate on identity fail closed on this)
    assert _resolve(tmp_path, {}) == ""
