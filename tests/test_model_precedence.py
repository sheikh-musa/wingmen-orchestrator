"""The BASH model-precedence cascade launch_dangerous_cc.sh uses to pick a lane's
model (#24392). Extracted into scripts/lib/model_precedence.sh so the SHIPPED path
is testable (gate-test != shipped-path): this drives the ACTUAL function, not a
transcription.

Precedence (highest first):
  MODEL env > .<session>_model > .group_default_model.<family> > .fleet_model > opus
The function echoes "<model>\\t<tier>" so the launcher can print WHICH tier won.

The group tier delegates to the tested python resolver (scripts.lib.lane_model_
resolver); the session/fleet/default tiers are bash file reads. With NO group file
present the resolution is byte-identical to the pre-#24392 behaviour (back-compat).
"""
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "model_precedence.sh")
_OPUS = "claude-opus-4-8"


def _resolve(session, orch_dir, *, model_env=None, fleet_file=None):
    """Drive the real bash function; return (model, tier)."""
    fleet_file = fleet_file if fleet_file is not None else os.path.join(orch_dir, ".fleet_model")
    env = dict(os.environ)
    env.pop("MODEL", None)
    if model_env is not None:
        env["MODEL"] = model_env
    out = subprocess.run(
        ["bash", "-c",
         'source "$1"; resolve_lane_model "$2" "$3" "$4" "$5" "$6"',
         "_", _LIB, session, orch_dir, sys.executable, fleet_file, _OPUS],
        capture_output=True, text=True, cwd=_ROOT, env=env,
    )
    assert out.returncode == 0, out.stderr
    model, _, tier = out.stdout.rstrip("\n").partition("\t")
    return model, tier


@pytest.fixture
def orch(tmp_path):
    def write(name, value):
        (tmp_path / name).write_text(value + "\n")
    return str(tmp_path), write


# ── precedence, highest tier first ────────────────────────────────────────────

def test_model_env_wins_over_everything(orch):
    orch_dir, write = orch
    write(".irsyad-coord_model", "claude-sonnet-5")
    write(".group_default_model.irsyad", "claude-sonnet-5")
    write(".fleet_model", "claude-sonnet-5")
    assert _resolve("irsyad-coord", orch_dir, model_env="claude-opus-5") == ("claude-opus-5", "MODEL env")


def test_session_pointer_beats_group_and_fleet(orch):
    orch_dir, write = orch
    write(".irsyad-coord_model", "claude-opus-5")
    write(".group_default_model.irsyad", "claude-sonnet-5")
    write(".fleet_model", "claude-sonnet-5")
    assert _resolve("irsyad-coord", orch_dir) == ("claude-opus-5", ".irsyad-coord_model")


def test_group_tier_beats_fleet_for_new_lane(orch):
    """THE #24392 FIX: a NEW lane (no .<session>_model) with a family group model
    gets the FAMILY model, NOT the fleet-wide .fleet_model Sonnet flip."""
    orch_dir, write = orch
    write(".group_default_model.irsyad", "claude-opus-5")
    write(".fleet_model", "claude-sonnet-5")
    model, tier = _resolve("irsyad-coord", orch_dir)
    assert model == "claude-opus-5"
    assert tier.startswith(".group_default_model")


def test_fleet_model_when_no_session_or_group(orch):
    """Back-compat: a new lane in a family with NO group file still falls to
    .fleet_model exactly as before the fix."""
    orch_dir, write = orch
    write(".fleet_model", "claude-sonnet-5")
    assert _resolve("cosem-tdu", orch_dir) == ("claude-sonnet-5", ".fleet_model")


def test_hardcoded_default_when_nothing_set(orch):
    """No MODEL, no pointer, no group, no .fleet_model -> the opus-4-8 default."""
    orch_dir, _write = orch
    model, tier = _resolve("cosem-tdu", orch_dir)
    assert model == _OPUS
    assert "default" in tier


def test_singleton_body_skips_group_tier_falls_to_fleet(orch):
    """A no-pointer singleton (fleet-health) never uses the group tier even if a
    stray .group_default_model.fleet exists — it falls to .fleet_model. (Its real
    model is env/per-body driven; this only asserts the group tier is not consulted
    for it, matching the python resolver's body classification.)"""
    orch_dir, write = orch
    write(".group_default_model.fleet", "claude-sonnet-5")
    write(".fleet_model", "claude-opus-4-8")
    model, tier = _resolve("fleet-health", orch_dir)
    assert tier != ".group_default_model.<family>"


def test_empty_session_falls_to_fleet(orch):
    """Run outside tmux (empty session) -> no session/group tier, uses .fleet_model."""
    orch_dir, write = orch
    write(".fleet_model", "claude-sonnet-5")
    assert _resolve("", orch_dir) == ("claude-sonnet-5", ".fleet_model")
