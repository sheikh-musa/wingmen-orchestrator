"""Phase-0 tests for the unified reset+key-switch safety contract (op#14474).

Covers the gate library (scripts/lib/reset_gates.sh + handoff_freshness.sh) and the
registry (config/body_registry.json). Zero-behaviour-change phase: these prove the
gates BEHAVE (fail-closed) in isolation before any live path is wired to them.

Test map to the design's matrix (reports/reset-keyswitch-safety-contract-design-20260818.md §6):
  T2 stale-handoff refuses · T5 every registry body reachable/complete ·
  T6 deploy-contract-version refuses on stale · T7 a gate that cannot evaluate REFUSES ·
  plus Q3 per-gate force-flag parsing.
"""
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib" / "reset_gates.sh"
REGISTRY = REPO / "config" / "body_registry.json"

REQUIRED_FIELDS = [
    "host", "launcher", "dir", "tmux_session", "token_pointer",
    "is_singleton", "resume_mode", "boot_role",
]


def _run(snippet: str, env=None):
    """Source the gate lib and run a bash snippet; return (rc, stdout, stderr)."""
    full = f'set -uo pipefail; source "{LIB}"; {snippet}'
    p = subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


# --- T2: fresh-handoff gate ------------------------------------------------------

class TestFreshHandoff:
    def test_fresh_passes(self, tmp_path):
        h = tmp_path / "handoff.md"; h.write_text("fresh")
        rc, out, err = _run(f'require_fresh_handoff "{h}" 1800 0')
        assert rc == 0, err
        assert "OK" in out

    def test_stale_refuses(self, tmp_path):
        h = tmp_path / "handoff.md"; h.write_text("old")
        old = time.time() - 3 * 3600
        os.utime(h, (old, old))
        rc, out, err = _run(f'require_fresh_handoff "{h}" 1800 0')
        assert rc == 3
        assert "FAIL" in err and "stale" in err

    def test_stale_with_force_passes_loudly(self, tmp_path):
        h = tmp_path / "handoff.md"; h.write_text("old")
        old = time.time() - 3 * 3600
        os.utime(h, (old, old))
        rc, out, err = _run(f'require_fresh_handoff "{h}" 1800 1')
        assert rc == 0
        assert "WARNING" in err and "force" in err

    def test_missing_refuses(self, tmp_path):
        rc, out, err = _run(f'require_fresh_handoff "{tmp_path}/nope.md" 1800 0')
        assert rc == 3
        assert "MISSING" in err


# --- T6: deploy-contract-version (G7) --------------------------------------------

class TestContractVersion:
    def test_equal_passes(self):
        rc, out, err = _run('gate_contract_version 1 1')
        assert rc == 0 and "OK" in out

    def test_newer_actual_passes(self):
        rc, out, err = _run('gate_contract_version 1 3')
        assert rc == 0

    def test_older_actual_refuses(self):
        rc, out, err = _run('gate_contract_version 2 1')
        assert rc == 7 and "predates" in err

    def test_uses_lib_version_by_default(self):
        # actual defaults to the lib's own CONTRACT_VERSION (>=1) -> requiring 1 passes
        rc, out, err = _run('gate_contract_version 1')
        assert rc == 0

    def test_unreadable_actual_refuses(self):
        # T7 fail-closed: a non-numeric deployed version cannot be evaluated -> REFUSE
        rc, out, err = _run('gate_contract_version 1 ""')
        assert rc == 7 and "REFUSING" in err


# --- Q3: per-gate force flags ----------------------------------------------------

class TestForceFlags:
    def test_force_stale_only(self):
        rc, out, err = _run('reset_gates_parse_force --force-stale; force_for stale && echo STALE_FORCED; force_for busy || echo BUSY_NOT')
        assert "STALE_FORCED" in out and "BUSY_NOT" in out

    def test_force_all(self):
        rc, out, err = _run('reset_gates_parse_force --force-all; force_for stale && force_for busy && echo BOTH')
        assert "BOTH" in out

    def test_blunt_force_maps_to_all_with_warning(self):
        rc, out, err = _run('reset_gates_parse_force --force; force_for stale && force_for busy && echo BOTH')
        assert "BOTH" in out
        assert "WARNING" in err and "blunt --force" in err

    def test_no_force_by_default(self):
        rc, out, err = _run('reset_gates_parse_force; force_for stale || echo NONE')
        assert "NONE" in out


# --- T5: registry completeness / reachability ------------------------------------

class TestRegistry:
    def test_every_body_has_required_fields(self):
        import json
        d = json.loads(REGISTRY.read_text())
        bodies = {k: v for k, v in d["bodies"].items() if not k.startswith("_")}
        assert bodies, "registry has no real bodies"
        for body, row in bodies.items():
            for field in REQUIRED_FIELDS:
                assert field in row, f"{body} missing required field {field}"

    def test_registry_field_lookup(self):
        rc, out, err = _run(f'registry_field cc-orchestrator resume_mode "{REGISTRY}"')
        assert rc == 0 and out.strip() == "--continue", (out, err)

    def test_missing_body_refuses(self):
        rc, out, err = _run(f'registry_field not-a-body host "{REGISTRY}"')
        assert rc == 3 and "not in registry" in err

    def test_hub_is_first_class(self):
        # the hole today: cc-orchestrator was in NO switch tool. It MUST be in the registry.
        rc, out, err = _run(f'registry_bodies "{REGISTRY}"')
        assert rc == 0
        assert "cc-orchestrator" in out.split()

    def test_templates_excluded_from_bodies(self):
        rc, out, err = _run(f'registry_bodies "{REGISTRY}"')
        assert "_lane_template" not in out.split()


# --- T1 (static form): dry-run guard precedes every keystroke --------------------

import re
import pytest

RESET_SCRIPTS = sorted((REPO / "scripts").glob("reset_*.sh"))


class TestDryRunStaticInvariant:
    """The exact 2026-08-18 defect class: a reset that sends keystrokes must stop for
    RESET_DRYRUN BEFORE the first send-keys. If the guard is positioned after (or
    missing), a 'dry run' clears for real — which is what cleared the hub. This is a
    source-static contract check over every reset_*.sh, independent of host/role/env.
    """

    @pytest.mark.parametrize("script", RESET_SCRIPTS, ids=lambda p: p.name)
    def test_dryrun_stop_precedes_first_send_keys(self, script):
        # strip inline/full-line comments so prose like "# It send-keys into tmux"
        # is not mistaken for an actual `tmux send-keys` COMMAND.
        code = [l.split("#", 1)[0] for l in script.read_text().splitlines()]
        send_keys = [i for i, l in enumerate(code) if "send-keys" in l]
        if not send_keys:
            pytest.skip(f"{script.name} sends no keystrokes")
        first_keystroke = min(send_keys)

        # find the dry-run guard condition, then its exit (comment-stripped code)
        guard = next((i for i, l in enumerate(code)
                      if re.search(r'RESET_DRYRUN.{0,12}=.{0,4}1', l)), None)
        assert guard is not None, (
            f"{script.name} sends keystrokes but has NO RESET_DRYRUN guard — a 'dry run' "
            f"would clear for real (the 2026-08-18 hub-clear defect class).")
        exit_line = next((i for i in range(guard, len(code))
                          if re.search(r'\bexit\b', code[i])), None)
        assert exit_line is not None and exit_line < first_keystroke, (
            f"{script.name}: RESET_DRYRUN guard/exit (line ~{(exit_line or guard)+1}) does NOT "
            f"precede the first send-keys (line {first_keystroke+1}) — dry-run would send keystrokes.")
