"""Tests for scripts/deploy_cc_family.sh — TASK-045.

Pattern mirrors tests/test_check_lock_keys.py: invoke the script via
subprocess with fixtures under tmp_path, assert on exit code + stderr.

Fixtures override the repo-root and projects-root via env vars so the
script can be tested in isolation without touching ~/wingmen/projects.
"""
import os
import stat
import subprocess
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _REPO_ROOT / "scripts" / "deploy_cc_family.sh"


def _run(args: list[str], env_overrides: Optional[dict] = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_is_executable():
    """CI and operators need the script to run without explicit `bash` prefix."""
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/deploy_cc_family.sh must be +x"


def test_no_args_shows_usage():
    """No family-id → usage message + non-zero exit."""
    r = _run([])
    assert r.returncode != 0
    assert "usage" in r.stderr.lower() or "usage" in r.stdout.lower()
    assert "family-id" in (r.stderr + r.stdout).lower()
