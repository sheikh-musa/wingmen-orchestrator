"""The hourly monitor wrapper must stay EXECUTABLE in git (orch-console #89139).

Why this test exists: the wrapper lived untracked on gzb until 2026-11-01. I edited a comment
with the atomic pattern (write .tmp, os.replace), which preserves bytes but not file mode --
the replacement came out 0644, cron got "Permission denied", and the 05:23Z tick of a
minors'-PII monitor never started. A correct file that a scheduler cannot exec looks identical
to a correct file in a `cat`.

So the mode is asserted two ways:
  * the git INDEX mode is 100755 -- the mode that travels with a clone or a pull, which is what
    makes `git pull` a safe deploy;
  * the WORKING-TREE file is executable -- the mode cron actually sees on this host.

A test that only stat()s the working tree would pass after a local chmod while the pushed file
stayed 0644, and the next host to pull would inherit the broken mode. Both checks are needed.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WRAPPER_REL = "scripts/irsyad_pii_containment_monitor_tick.sh"
WRAPPER = REPO / WRAPPER_REL


def test_wrapper_exists():
    assert WRAPPER.is_file(), f"{WRAPPER_REL} must ship in the repo, not live untracked on a host"


def test_wrapper_is_executable_in_the_working_tree():
    """What cron sees on the host that runs it."""
    mode = WRAPPER.stat().st_mode
    assert mode & 0o111, (
        f"{WRAPPER_REL} is not executable (mode {oct(mode & 0o777)}) -- cron would fail with "
        "'Permission denied' and the tick would never start"
    )


def test_wrapper_mode_is_100755_in_git():
    """What every future clone/pull inherits. 100644 here breaks a host that has not chmod'd."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-s", "--", WRAPPER_REL],
            cwd=REPO, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover - environment guard
        pytest.skip(f"git unavailable: {e}")
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("not a git checkout (or file not yet tracked)")
    index_mode = out.stdout.split()[0]
    assert index_mode == "100755", (
        f"git index mode is {index_mode}, expected 100755 -- run "
        f"'git update-index --chmod=+x {WRAPPER_REL}' so a pull deploys the mode too"
    )


def test_wrapper_logs_the_tick_header_and_propagates_the_exit_code(tmp_path):
    """Run the wrapper against a stub monitor -- no silo, no DB, no secrets.

    Two properties the cron log depends on: a header line per tick (so a tick that never
    started is visible as a missing clause) and the monitor's real code on the `exit=` line.
    The wrapper must ALSO return that code, which it did not until now: the trailing `echo`
    used to be the script's own status, so the wrapper always exited 0.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    name = Path(WRAPPER_REL).name
    shutil.copy2(WRAPPER, scripts / name)
    (scripts / "irsyad_pii_containment_monitor.py").write_text("import sys\nsys.exit(2)\n")

    env = {**os.environ, "PII_MONITOR_ENV_FILE": str(tmp_path / "absent.env")}
    r = subprocess.run(["bash", str(scripts / name)], capture_output=True, text=True,
                       env=env, timeout=60)

    assert r.stdout.startswith("=== 20"), f"missing per-tick header line; stdout={r.stdout!r}"
    assert "exit=2" in r.stdout, f"the monitor's code must reach the log; stdout={r.stdout!r}"
    assert r.returncode == 2, "the wrapper must propagate the monitor's exit code, not mask it"
