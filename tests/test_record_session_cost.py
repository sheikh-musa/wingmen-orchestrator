"""CLI contract tests for scripts/record_session_cost.py.

Verifies argument parsing + error handling. Real DB writes are
covered by integration tests in test_boot_briefing_substrate_visibility.py
(adding one round-trip test in step 3.4 below).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "record_session_cost.py"


def test_no_args_returns_usage_error():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "usage" in result.stderr.lower() or "usage" in result.stdout.lower() or "required" in (result.stderr + result.stdout).lower()


def test_help_flag_exits_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "session" in out.lower()
    assert "cc_identity" in out.lower() or "--cc-identity" in out.lower()


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} should be executable"


def test_invalid_source_rejected():
    """source must be one of: claude_code_cli / anthropic_api / claude_ai_web / unknown"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--cc-identity", "cc-orchestrator",
         "--input-tokens", "100",
         "--output-tokens", "50",
         "--source", "invalid_source_value"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "source" in out.lower() or "invalid" in out.lower() or "choice" in out.lower()
