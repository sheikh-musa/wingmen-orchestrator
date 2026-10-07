"""Regression test for lanes.sh's pre-boot tmux-session guard (bus #56370).

THE BUG: `boot_one`'s existence check was `tmux has-session -t "$name"`. tmux's
target matching is a PREFIX match when the target has no leading '=', so
'cosem-tdu' matched the running 'cosem-tdu-coord' session and the guard
false-positived "tmux session already exists" — permanently blocking the
'cosem-tdu' builder lane from booting via lanes.sh while 'cosem-tdu-coord' was
up (order-dependent: only bites once the longer-named session exists first).

Fix: exact-match via the leading '=' (`tmux has-session -t "=$name"`).

This test stubs `tmux` itself (not pgrep/lsof) to emulate tmux's real prefix-
vs-exact `has-session` semantics, and stubs `pgrep` to report no running
claude (so `dir_has_claude` is false and the tmux guard is what's under
test). `new-session` is stubbed too, so a passing guard never spawns a real
process — it just proves boot_one proceeded past the guard.
"""
import os
import stat
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LANES_SH = _REPO / "scripts" / "lanes.sh"


def _make_stubs(tmp_path: Path, live_sessions: list[str]) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n")
    sessions = " ".join(live_sessions)
    (bindir / "tmux").write_text(
        "#!/usr/bin/env bash\n"
        f'LIVE="{sessions}"\n'
        'case "$1" in\n'
        '  has-session)\n'
        '    target="$3"\n'
        '    if [[ "$target" == "="* ]]; then\n'
        '      want="${target#=}"\n'
        '      for s in $LIVE; do [ "$s" = "$want" ] && exit 0; done\n'
        '      exit 1\n'
        '    else\n'
        '      for s in $LIVE; do case "$s" in "$target"*) exit 0 ;; esac; done\n'
        '      exit 1\n'
        '    fi\n'
        '    ;;\n'
        '  new-session) echo "NEW-SESSION-CALLED: $*" ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n'
    )
    for f in ("pgrep", "tmux"):
        p = bindir / f
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _run_boot_one(tmp_path: Path, live_sessions: list[str], boot_name: str) -> str:
    bindir = _make_stubs(tmp_path, live_sessions)
    lane_dir = tmp_path / "lane-dir"
    lane_dir.mkdir()
    script = (
        f'export PATH="{bindir}:$PATH"\n'
        f'source "{_LANES_SH}"\n'
        f'boot_one "{boot_name}" "{lane_dir}" ""\n'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, f"unexpected crash: {r.stderr}"
    return r.stdout


def test_prefix_colliding_session_does_not_false_skip(tmp_path):
    # 'cosem-tdu-coord' is live; booting 'cosem-tdu' must NOT be skipped as
    # "already exists" (the bug) -- it must proceed to new-session.
    out = _run_boot_one(tmp_path, ["cosem-tdu-coord"], "cosem-tdu")
    assert "SKIP" not in out, out
    assert "NEW-SESSION-CALLED" in out, out


def test_exact_name_collision_still_skips(tmp_path):
    # The real positive case must still be caught: an exact-name session is live.
    out = _run_boot_one(tmp_path, ["cosem-tdu"], "cosem-tdu")
    assert "SKIP cosem-tdu" in out, out
    assert "NEW-SESSION-CALLED" not in out, out


def test_no_collision_boots(tmp_path):
    out = _run_boot_one(tmp_path, [], "cosem-tdu")
    assert "SKIP" not in out, out
    assert "NEW-SESSION-CALLED" in out, out
