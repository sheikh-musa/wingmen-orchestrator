"""Regression tests for lanes.sh lane-liveness detection (bus #44251 / #44258).

THE BUG: `dir_has_claude` was `running_cwds | grep -Fxq "$target"`. Under the
script's `set -euo pipefail`, when a lane IS running, `grep -q` matches and exits
immediately, SIGPIPE-ing the still-writing `running_cwds` on the left of the pipe;
`pipefail` then propagates that non-zero exit and `dir_has_claude` returns FALSE
*despite the match*. So a live lane was reported "down" — and because `boot_one`
uses the same predicate as its pre-boot guard, `lanes.sh up` could then launch a
SECOND `claude` into a live lane's worktree (the double-boot hazard: e.g. the
fleet console's Boot button on a cosem lane holding real-org work).

These tests stub `pgrep`/`lsof` with a synthetic pid whose cwd is a known lane
dir, positioned EARLY among many pids so the match short-circuit reliably
SIGPIPEs the producer — reproducing the failure deterministically on the old
code and proving the fix (snapshot-then-match, pipefail-safe) reports "up".
"""
import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_LANES_SH = _REPO / "scripts" / "lanes.sh"
_MATCH_DIR = "/tmp/wingmen-test/cosem-port-lane"
_N_PIDS = 60  # match emitted first, 59 more after → producer still writing when grep -q exits


def _make_stubs(tmp_path: Path, match_cwd: str) -> Path:
    """A stub bin dir: pgrep prints _N_PIDS fake pids; lsof prints the match cwd
    for pid 1 and a junk cwd for every other pid (so the match comes first)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "pgrep").write_text(
        "#!/usr/bin/env bash\n"
        f"seq 1 {_N_PIDS}\n"
    )
    # lsof invoked as: lsof -a -p <pid> -d cwd -Fn  → emit an -Fn 'n<cwd>' line.
    (bindir / "lsof").write_text(
        "#!/usr/bin/env bash\n"
        "pid=\"\"; while [ $# -gt 0 ]; do [ \"$1\" = -p ] && { pid=\"$2\"; shift; }; shift; done\n"
        f"if [ \"$pid\" = 1 ]; then echo 'n{match_cwd}'; else echo 'n/tmp/wingmen-test/other-$pid'; fi\n"
    )
    for f in ("pgrep", "lsof"):
        os.chmod(bindir / f, 0o755)
    return bindir


def _dir_has_claude(tmp_path: Path, target: str) -> int:
    """Return dir_has_claude's exit code for `target` with stubbed pgrep/lsof,
    under the SAME `set -euo pipefail` the real script runs with."""
    bindir = _make_stubs(tmp_path, _MATCH_DIR)
    script = (
        f'export PATH="{bindir}:$PATH"\n'
        f'source "{_LANES_SH}"\n'          # source-guard keeps the CLI dispatch from firing
        f'if dir_has_claude "{target}"; then exit 0; else exit 1; fi\n'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode in (0, 1), f"unexpected crash rc={r.returncode}: {r.stderr}"
    return r.returncode


def test_running_lane_reported_up_under_pipefail(tmp_path):
    # A live claude's cwd == the lane dir → UP (0). This is the regression: the
    # old piped `grep -q` returned 1 ("down") here because of the SIGPIPE/pipefail race.
    assert _dir_has_claude(tmp_path, _MATCH_DIR) == 0


def test_absent_lane_reported_down(tmp_path):
    # No running cwd matches this dir → down (1).
    assert _dir_has_claude(tmp_path, "/tmp/wingmen-test/not-a-live-lane") == 1


def test_nested_worktree_cwd_counts_as_up(tmp_path):
    # A claude whose cwd is nested UNDER the lane dir (a subdir or nested git
    # worktree) still means the lane is occupied → UP (console: "cwd in the lane
    # dir (or its worktrees)"). Here the lane dir is a parent of the match cwd.
    assert _dir_has_claude(tmp_path, "/tmp/wingmen-test") == 0


# --- fail-CLOSED when lsof cannot be found -------------------------------------
#
# `lsof` lives at /usr/sbin/lsof and /usr/sbin is absent from the PATH a launchd job, a
# cron entry, or a reduced tool shell hands down. The old code shelled out to bare `lsof`,
# got "command not found" on stderr, captured an EMPTY stdout, and returned "no claude
# running" for EVERY lane — so `boot_one`'s guard passed and a SECOND dangerous-CC could be
# launched onto a live worktree. An unanswerable probe must say UNKNOWN, never "free".

_NO_LSOF_PATH = "/nonexistent-bin:/usr/bin:/bin"


def _probe_without_lsof(target: str) -> subprocess.CompletedProcess:
    """Run dir_has_claude under a PATH with no lsof AND no system fallback... which cannot
    be done where /usr/sbin/lsof exists, so resolve_lsof is stubbed out instead: the probe
    is forced to fail exactly as a missing binary would."""
    script = (
        f'source "{_LANES_SH}"\n'
        "resolve_lsof() { return 1; }\n"        # simulate: no lsof anywhere
        f'if dir_has_claude "{target}"; then echo YES; else echo "rc=$?"; fi\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_unresolvable_lsof_is_unknown_not_down():
    r = _probe_without_lsof("/tmp/wingmen-test/cosem-port-lane")
    # dir_has_claude's own rc must be 2 (UNKNOWN) — not 1 (free to boot).
    assert r.stdout.strip() == "rc=2", f"expected UNKNOWN (2), got {r.stdout!r} / {r.stderr!r}"
    assert "UNKNOWN" in r.stderr  # and it must say so out loud


def test_boot_one_refuses_when_probe_fails(tmp_path):
    # The guard that actually matters: a lane dir that EXISTS, an unanswerable liveness
    # probe → boot_one must REFUSE, not fall through to `tmux new-session`.
    lane = tmp_path / "lane"
    lane.mkdir()
    script = (
        f'source "{_LANES_SH}"\n'
        "resolve_lsof() { return 1; }\n"
        'tmux() { echo "TMUX-CALLED $*"; return 0; }\n'   # any boot attempt calls this
        f'boot_one "test-lane" "{lane}" ""; echo "rc=$?"\n'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert "TMUX-CALLED" not in r.stdout, f"boot_one BOOTED despite an unknown probe: {r.stdout}"
    assert "REFUSE" in r.stdout + r.stderr
    # Under the script's own `set -e`, boot_one's non-zero return aborts the caller, so the
    # shell exits 4 before the `echo` — the loud non-zero exit IS the assertion.
    assert r.returncode == 4, f"expected the run to abort with 4, got {r.returncode}"


def test_resolve_lsof_prefers_path_then_falls_back_to_system(tmp_path):
    # PATH first, so a stubbed bin dir still wins (the tests above depend on it)...
    bindir = _make_stubs(tmp_path, _MATCH_DIR)
    script = f'export PATH="{bindir}:$PATH"\nsource "{_LANES_SH}"\nresolve_lsof\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.stdout.strip() == str(bindir / "lsof")
    # ...and with no lsof on PATH it still finds a system one where one exists.
    script = (
        f'export PATH="{_NO_LSOF_PATH}"\nsource "{_LANES_SH}"\n'
        'if out="$(resolve_lsof)"; then echo "FOUND $out"; else echo NONE; fi\n'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    if os.path.exists("/usr/sbin/lsof") or os.path.exists("/usr/bin/lsof"):
        assert r.stdout.startswith("FOUND /"), r.stdout
    else:
        assert r.stdout.strip() == "NONE"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
