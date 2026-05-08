"""Tests for scripts/measure_supabase_branch_coldstart.

Verifies argument parsing + exit codes. Real timing/network behavior is not
mocked — assumed to work because psycopg.connect is well-tested. We only
verify the script's CLI contract.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "measure_supabase_branch_coldstart.py"


def test_no_args_returns_2():
    """Missing connection string → usage error → exit 2."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_invalid_dsn_returns_2():
    """Connection failure surfaces as exit 2."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "postgresql://nope:nope@nonexistent.invalid:5432/none"],
        capture_output=True, text=True,
        timeout=120,  # connect_timeout=60 in script + a slack
    )
    assert result.returncode == 2
    assert "FAIL" in result.stdout or "FAIL" in result.stderr


def test_script_is_executable():
    """Sanity: file has +x bit so operator can run directly."""
    import os
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} should be executable"
