"""Tests for the safe-restart helper (BUG-016 / Job #83).

The orchestrator is supervised by launchd on the Mac Mini. Operators must
restart via `scripts/restart_orch.sh`, which uses `launchctl kickstart -k`.
`nohup` / background-shell restarts bypass launchd and produce zombie
pollers — these tests pin that contract in code.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "restart_orch.sh"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "restart.md"


def test_script_exists_and_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"{SCRIPT} is not user-executable"


def test_script_refuses_nohup(tmp_path):
    # Stub launchctl on PATH so even if the nohup guard failed we would not
    # perturb the real launchd domain.
    stub = tmp_path / "launchctl"
    stub.write_text("#!/bin/sh\necho 'stub launchctl called'\nexit 0\n")
    stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env["ORCH_LAUNCHD_LABEL"] = "dev.wingmen.orchestrator.test"

    result = subprocess.run(
        ["nohup", "bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode != 0, (
        f"expected non-zero exit under nohup, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "nohup" in combined, (
        f"expected refusal message mentioning 'nohup', got: {combined!r}"
    )


def test_script_uses_kickstart_not_unload_load():
    content = SCRIPT.read_text()
    assert "kickstart -k" in content, (
        "script must use `launchctl kickstart -k` as the restart primitive"
    )

    # The race-prone pattern is an executed `launchctl unload` somewhere in
    # the script body. Printed recovery instructions (via echo/printf) begin
    # with `echo`, not `launchctl`, and will not match this regex.
    live_unload = re.compile(r"^\s*launchctl\s+unload\b", re.MULTILINE)
    assert not live_unload.search(content), (
        "script must not execute `launchctl unload` — it races with KeepAlive"
    )
    live_load = re.compile(r"^\s*launchctl\s+load\b", re.MULTILINE)
    assert not live_load.search(content), (
        "script must not execute `launchctl load` paired with unload — "
        "use `launchctl kickstart -k` instead"
    )


def test_docs_forbid_nohup():
    assert RUNBOOK.exists(), f"{RUNBOOK} missing"
    text = RUNBOOK.read_text().lower()
    assert "nohup" in text, "runbook must mention nohup"
    assert "forbidden" in text or "never" in text, (
        "runbook must forbid nohup using 'forbidden' or 'never'"
    )
