"""The BASH subagent-model precedence cascade launch_dangerous_cc.sh uses to decide
whether to export CLAUDE_CODE_SUBAGENT_MODEL for a lane (#42821, op#22298 cost-rollout).

Mirrors scripts/lib/model_precedence.sh so the SHIPPED path is the TESTED path
(gate-test != shipped-path): this drives the ACTUAL function, not a transcription.

Precedence (highest first):
  CLAUDE_CODE_SUBAGENT_MODEL env > .<session>_subagent_model > .fleet_subagent_model > (unset)
The function echoes "<model>\\t<tier>", or NOTHING when no tier applies. DEFAULT-OFF by
construction: no env + no marker + no fleet file -> empty stdout -> the launcher never
exports the var -> byte-identical to today (subagents inherit the lane's main model).

CAI-1170 AUDITOR CLAMP: the FULL auditors (cc-quality / cc-storefront) render governance;
their audit subagents MUST NOT be downgraded to a cheap model. For an auditor lane the
resolver REFUSES every tier (incl. an explicit env) and echoes NOTHING (fail-closed),
warning LOUD — so a marker or a stray env can never cheapen an auditor's subagents.
"""
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "subagent_model_precedence.sh")
_HAIKU = "claude-haiku-4-5-20251001"  # DIRECT id, no alias (memory: no ANTHROPIC_DEFAULT_HAIKU_MODEL pin)


def _resolve(session, orch_dir, *, env_model=None, fleet_file=None):
    """Drive the real bash function; return (model, tier) or ('', '') when nothing applies."""
    fleet_file = fleet_file if fleet_file is not None else os.path.join(orch_dir, ".fleet_subagent_model")
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
    if env_model is not None:
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = env_model
    out = subprocess.run(
        ["bash", "-c",
         'source "$1"; resolve_subagent_model "$2" "$3" "$4"',
         "_", _LIB, session, orch_dir, fleet_file],
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


# ── default-off: nothing set -> nothing exported (byte-identical to today) ──────

def test_unset_when_no_env_no_marker_no_fleet(orch):
    orch_dir, _write = orch
    assert _resolve("scholar", orch_dir) == ("", "")


# ── precedence, highest tier first ─────────────────────────────────────────────

def test_env_wins_over_everything(orch):
    orch_dir, write = orch
    write(".scholar_subagent_model", _HAIKU)
    write(".fleet_subagent_model", _HAIKU)
    model, tier = _resolve("scholar", orch_dir, env_model="claude-sonnet-5")
    assert model == "claude-sonnet-5"
    assert tier == "env"


def test_session_marker_beats_fleet(orch):
    orch_dir, write = orch
    write(".scholar_subagent_model", _HAIKU)
    write(".fleet_subagent_model", "claude-sonnet-5")
    assert _resolve("scholar", orch_dir) == (_HAIKU, ".scholar_subagent_model")


def test_fleet_marker_when_no_session_marker(orch):
    orch_dir, write = orch
    write(".fleet_subagent_model", _HAIKU)
    assert _resolve("scholar", orch_dir) == (_HAIKU, ".fleet_subagent_model")


def test_empty_or_whitespace_marker_falls_through(orch):
    """A marker that is empty/whitespace is inert -> fall through (here: to unset)."""
    orch_dir, write = orch
    write(".scholar_subagent_model", "   ")
    assert _resolve("scholar", orch_dir) == ("", "")


def test_empty_session_uses_fleet_only(orch):
    """Run outside tmux (empty session) -> no per-session tier; fleet still applies."""
    orch_dir, write = orch
    write(".fleet_subagent_model", _HAIKU)
    assert _resolve("", orch_dir) == (_HAIKU, ".fleet_subagent_model")


# ── CAI-1170 auditor clamp: an auditor's subagents NEVER get a downgraded model ─

@pytest.mark.parametrize("sess", ["quality", "storefront", "cc-quality", "cc-storefront"])
def test_auditor_refuses_session_marker(orch, sess):
    orch_dir, write = orch
    write(f".{sess}_subagent_model", _HAIKU)
    write(f".{sess.replace('cc-', '')}_subagent_model", _HAIKU)  # bare-session form too
    model, tier = _resolve(sess, orch_dir)
    assert model == "", f"{sess} exported subagent model {model!r} — CAI-1170 violated"
    assert tier == ""


def test_auditor_refuses_fleet_marker(orch):
    orch_dir, write = orch
    write(".fleet_subagent_model", _HAIKU)
    assert _resolve("quality", orch_dir) == ("", "")


def test_auditor_refuses_env(orch):
    """Even an explicit env cannot cheapen an auditor's subagents (fail-closed)."""
    orch_dir, _write = orch
    assert _resolve("storefront", orch_dir, env_model=_HAIKU) == ("", "")


def test_auditor_clamp_warns_loud(orch):
    """When the clamp suppresses a present marker, it must warn LOUD (not silent)."""
    orch_dir, write = orch
    write(".quality_subagent_model", _HAIKU)
    out = subprocess.run(
        ["bash", "-c", 'source "$1"; resolve_subagent_model "$2" "$3" "$4"',
         "_", _LIB, "quality", orch_dir, os.path.join(orch_dir, ".fleet_subagent_model")],
        capture_output=True, text=True, cwd=_ROOT,
        env={k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SUBAGENT_MODEL"},
    )
    assert out.returncode == 0
    assert out.stdout.strip() == ""
    assert "1170" in out.stderr or "AUDITOR" in out.stderr, f"clamp must warn LOUD; stderr={out.stderr!r}"


def test_non_auditor_marker_still_exported(orch):
    """Regression guard: the clamp must NOT touch non-auditor lanes."""
    orch_dir, write = orch
    write(".scholar_subagent_model", _HAIKU)
    assert _resolve("scholar", orch_dir) == (_HAIKU, ".scholar_subagent_model")
