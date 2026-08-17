"""Canonical lane→GROUP-model resolver (#24392 new-lane model-precedence fix).

The defect: a NEW worker lane with no per-body `.<session>_model` pointer fell
straight through to `.fleet_model` (a fleet-wide Sonnet flip) instead of its
FAMILY's model — a silent capability downgrade that only bites new lanes. The fix
inserts a per-GROUP tier `.group_default_model.<family>` BETWEEN `.<session>_model`
and `.fleet_model`, mirroring the TOKEN resolver's per-group pointer (which already
gets family right — the same boot was token-right/model-wrong).

This module owns ONLY the group tier; the caller (launch_dangerous_cc.sh) keeps
handling MODEL env, `.<session>_model` and `.fleet_model` around it. The family is
derived with lane_token_resolver.family_of so the model group file keys off the
IDENTICAL family as the token group file (no twin family-map = twin-drift lesson).

Also exercises the __main__ CLI shim launch_dangerous_cc.sh calls, proving bash and
python get the SAME answer from the SAME code.
"""
import os
import subprocess
import sys

import pytest

from scripts.lib import lane_model_resolver as M


@pytest.fixture
def orch(tmp_path):
    """A fake $ORCH_DIR. Returns (orch_dir, grp) where grp(name, value) writes a
    dotfile with `value` as its contents (a model id, stored directly — not a
    pointer to another file, unlike the token resolver)."""
    def grp(name: str, value: str):
        (tmp_path / name).write_text(value + "\n")
    return str(tmp_path), grp


# ── group tier resolves ───────────────────────────────────────────────────────

def test_group_model_resolves_for_in_family_lanes(orch):
    """A `.group_default_model.irsyad` applies to EVERY lane in the irsyad family
    (bare, sub-tagged, and cc--prefixed) — but not to another family."""
    orch_dir, grp = orch
    grp(".group_default_model.irsyad", "claude-opus-5")
    for sess in ("irsyad", "irsyad-coord", "irsyad-prog2", "cc-irsyad-1"):
        assert M.resolve_lane_group_model(sess, orch_dir=orch_dir) == "claude-opus-5", sess
    # a DIFFERENT family with no group file -> None (falls through to .fleet_model)
    assert M.resolve_lane_group_model("cosem-tdu", orch_dir=orch_dir) is None


def test_no_group_file_returns_none(orch):
    """No `.group_default_model.<fam>` -> None (caller falls through to .fleet_model,
    byte-identical to the pre-fix behaviour)."""
    orch_dir, _grp = orch
    assert M.resolve_lane_group_model("irsyad", orch_dir=orch_dir) is None


# ── fail-open ─────────────────────────────────────────────────────────────────

def test_empty_group_file_returns_none(orch):
    """An empty `.group_default_model.<fam>` -> None (never an empty-string model)."""
    orch_dir, _grp = orch
    open(os.path.join(orch_dir, ".group_default_model.irsyad"), "w").close()
    assert M.resolve_lane_group_model("irsyad", orch_dir=orch_dir) is None


def test_whitespace_only_group_file_returns_none(orch):
    """A whitespace-only group file -> None (stripped to empty)."""
    orch_dir, grp = orch
    grp(".group_default_model.irsyad", "   ")
    assert M.resolve_lane_group_model("irsyad", orch_dir=orch_dir) is None


# ── body classification (mirrors the token resolver's non-worker bodies) ──────

def test_session_pointer_and_singleton_bodies_never_use_group_tier(orch):
    """nazim / cc-orchestrator (per-session bodies) and cai / fleet-health
    (no-pointer singletons) are env/per-body-model driven, NEVER the group tier —
    even if a stray group file exists for the name their family_of() reduces to."""
    orch_dir, grp = orch
    # family_of("fleet-health")=="fleet", family_of("cai")=="cai", etc. — a stray
    # file for any of those must be ignored for these bodies.
    grp(".group_default_model.fleet", "claude-sonnet-5")
    grp(".group_default_model.cai", "claude-sonnet-5")
    grp(".group_default_model.nazim", "claude-sonnet-5")
    for body in ("nazim", "cc-orchestrator", "cai", "fleet-health"):
        assert M.resolve_lane_group_model(body, orch_dir=orch_dir) is None, body


def test_empty_session_returns_none(orch):
    """Run outside tmux (empty session) -> None, never a crash."""
    orch_dir, _grp = orch
    assert M.resolve_lane_group_model("", orch_dir=orch_dir) is None


# ── family derivation is REUSED from lane_token_resolver (no twin map) ─────────

def test_family_derivation_is_shared_with_token_resolver():
    """The model resolver must derive family via lane_token_resolver.family_of so
    the model group file and the token group file key off the IDENTICAL family."""
    from scripts.lib import lane_token_resolver as T
    assert M.family_of is T.family_of


# ── CLI shim (the rail launch_dangerous_cc.sh calls) ─────────────────────────

def test_cli_shim_matches_python_api(orch):
    """`python3 -m scripts.lib.lane_model_resolver --session S` prints EXACTLY the
    model resolve_lane_group_model returns (bash gets the same answer)."""
    orch_dir, grp = orch
    grp(".group_default_model.irsyad", "claude-opus-5")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(M.__file__))))
    for session, expect in [("irsyad-coord", "claude-opus-5"), ("cosem-tdu", None),
                            ("cai", None)]:
        out = subprocess.run(
            [sys.executable, "-m", "scripts.lib.lane_model_resolver",
             "--session", session, "--orch-dir", orch_dir],
            capture_output=True, text=True, cwd=repo_root,
        )
        assert out.returncode == 0
        assert out.stdout.strip() == (expect or ""), (session, out.stdout, expect)


def test_cli_shim_prints_nothing_for_none(orch):
    """A None resolution prints EMPTY stdout (so `$(...)` is empty in bash)."""
    orch_dir, _grp = orch
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(M.__file__))))
    out = subprocess.run(
        [sys.executable, "-m", "scripts.lib.lane_model_resolver",
         "--session", "irsyad", "--orch-dir", orch_dir],
        capture_output=True, text=True, cwd=repo_root,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == ""
