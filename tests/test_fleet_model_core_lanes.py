"""Tests for scripts/lib/protected_sessions_guard.sh's core_lanes_or_refuse()
(op#42896/#42909 P1, bus #43051 fail-open fix).

No bash-testing harness exists yet in this repo for .sh files; this uses a fake
VENV_PY (a tiny stub script standing in for the real
`python -m nervous_system.protected_agents sessions` CLI) so the function is
exercised via subprocess without touching live tmux state or the real DB --
exactly the isolation lane_winddown.py's own docstring calls out as the design
goal for this class of predicate.
"""
import os
import stat
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO_ROOT, "scripts", "lib", "protected_sessions_guard.sh")
AUDITOR_LANES_LIB = os.path.join(REPO_ROOT, "scripts", "lib", "auditor_lanes.sh")


def _make_stub(tmp_path, output: str, exit_code: int = 0) -> str:
    """A fake VENV_PY: ignores its argv (-m nervous_system.protected_agents
    sessions), prints `output`, exits `exit_code`."""
    stub = tmp_path / "fake_venv_py.sh"
    stub.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' {output!r}\nexit {exit_code}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(stub)


def _run(tmp_path, output: str, exit_code: int = 0, auditor_lanes: str = "quality storefront"):
    venv_py = _make_stub(tmp_path, output, exit_code)
    script = f'''
set -uo pipefail
VENV_PY={venv_py!r}
AUDITOR_LANES={auditor_lanes!r}
source {AUDITOR_LANES_LIB!r}
source {GUARD!r}
core_lanes_or_refuse
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_healthy_read_returns_core_lanes_minus_auditors(tmp_path):
    rc, out, err = _run(tmp_path, "cai fleet-console fleet-health nazim orch orchestrator quality")
    assert rc == 0
    core = out.split()
    assert set(core) == {"cai", "fleet-console", "fleet-health", "nazim", "orch", "orchestrator"}
    assert "quality" not in core  # auditor lane excluded, not blanket-skipped


def test_cli_nonzero_exit_refuses(tmp_path):
    rc, out, err = _run(tmp_path, "", exit_code=1)
    assert rc != 0
    assert "refusing" in err.lower()


def test_empty_output_refuses(tmp_path):
    rc, out, err = _run(tmp_path, "")
    assert rc != 0
    assert "refusing" in err.lower()


def test_fleet_console_only_refuses(tmp_path):
    """The EXACT scenario bus #43051 named: a registry read that 'succeeds' but
    is missing the real singletons, so the CLI prints only the one non-agent
    session. A naive empty-output check would NOT catch this (the string isn't
    empty) -- this is the regression test for that gap."""
    rc, out, err = _run(tmp_path, "fleet-console")
    assert rc != 0
    assert "refusing" in err.lower()
    assert "cai" in err  # names which required member is missing


def test_missing_orch_only_refuses(tmp_path):
    """Same failure class, the other required name: everything except 'orch'."""
    rc, out, err = _run(tmp_path, "cai fleet-console fleet-health nazim quality")
    assert rc != 0
    assert "orch" in err
