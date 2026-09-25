"""The BASH subagent-model cascade launch_dangerous_cc.sh uses to decide whether to export
CLAUDE_CODE_SUBAGENT_MODEL for a lane (#42821, op#22298; hardened per the #152 review).

Two layers, both tested against the SHIPPED functions (shipped path == tested path):
  * _resolve_subagent_model_raw  — the pure cascade (echo), unit-tested in a subshell.
  * apply_subagent_model         — the SOURCED, side-effecting entry the launcher calls;
                                   tested by inspecting the ACTUAL exported env a CHILD
                                   process would inherit (not the function's echo).

Precedence: CLAUDE_CODE_SUBAGENT_MODEL env > .<session>_subagent_model > .fleet_subagent_model > unset.
Default-off: nothing set -> nothing exported (byte-identical to today).

Fail-closed guards (they compound):
  (1) UNRESOLVED session -> can't prove non-auditor -> export nothing + scrub inherited + warn.
  (2) CAI-1170 auditor    -> never a downgraded subagent model; scrub ANY inherited value + warn.
"""
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "subagent_model_precedence.sh")
_HAIKU = "claude-haiku-4-5-20251001"  # DIRECT id, no alias


# ── layer 1: the pure cascade (echo) ────────────────────────────────────────────

def _raw(session, orch_dir, *, env_model=None, fleet_file=None):
    fleet_file = fleet_file if fleet_file is not None else os.path.join(orch_dir, ".fleet_subagent_model")
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
    if env_model is not None:
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = env_model
    out = subprocess.run(
        ["bash", "-c", 'source "$1"; _resolve_subagent_model_raw "$2" "$3" "$4"',
         "_", _LIB, session, orch_dir, fleet_file],
        capture_output=True, text=True, cwd=_ROOT, env=env,
    )
    assert out.returncode == 0, out.stderr
    model, _, tier = out.stdout.rstrip("\n").partition("\t")
    return model, tier


# ── layer 2: the shipped apply (real exported env a child inherits) ──────────────

def _apply_childenv(session, orch_dir, *, env_model=None):
    """Run the SOURCED apply, then read CLAUDE_CODE_SUBAGENT_MODEL from a CHILD process's
    environment (proves inheritance / unset, per the #152 review). Returns (childval, stderr)
    where childval is the string or the sentinel '__ABSENT__' when the var is unset."""
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
    if env_model is not None:
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = env_model
    script = (
        'source "$1"; apply_subagent_model "$2" "$3"; '
        # a genuine CHILD process reads the inherited env (not the function's echo)
        r'bash -c '"'"'printf "CHILDENV=%s\n" "${CLAUDE_CODE_SUBAGENT_MODEL-__ABSENT__}"'"'"''
    )
    out = subprocess.run(
        ["bash", "-c", script, "_", _LIB, session, orch_dir],
        capture_output=True, text=True, cwd=_ROOT, env=env,
    )
    assert out.returncode == 0, out.stderr
    line = [l for l in out.stdout.splitlines() if l.startswith("CHILDENV=")][0]
    return line[len("CHILDENV="):], out.stderr


@pytest.fixture
def orch(tmp_path):
    def write(name, value):
        (tmp_path / name).write_text(value + "\n")
    return str(tmp_path), write


# ── cascade precedence + default-off ────────────────────────────────────────────

def test_raw_unset_when_nothing_set(orch):
    orch_dir, _ = orch
    assert _raw("scholar", orch_dir) == ("", "")


def test_raw_env_wins(orch):
    orch_dir, write = orch
    write(".scholar_subagent_model", _HAIKU)
    write(".fleet_subagent_model", _HAIKU)
    assert _raw("scholar", orch_dir, env_model="claude-sonnet-5") == ("claude-sonnet-5", "env")


def test_raw_session_beats_fleet(orch):
    orch_dir, write = orch
    write(".scholar_subagent_model", _HAIKU)
    write(".fleet_subagent_model", "claude-sonnet-5")
    assert _raw("scholar", orch_dir) == (_HAIKU, ".scholar_subagent_model")


def test_raw_fleet_when_no_session_marker(orch):
    orch_dir, write = orch
    write(".fleet_subagent_model", _HAIKU)
    assert _raw("scholar", orch_dir) == (_HAIKU, ".fleet_subagent_model")


def test_raw_whitespace_marker_falls_through(orch):
    orch_dir, write = orch
    write(".scholar_subagent_model", "   ")
    assert _raw("scholar", orch_dir) == ("", "")


# ── shipped apply: default-off + non-auditor export/escape-hatch ─────────────────

def test_apply_default_off_no_export(orch):
    orch_dir, _ = orch
    childval, _err = _apply_childenv("scholar", orch_dir)
    assert childval == "__ABSENT__"


def test_apply_non_auditor_marker_exported(orch):
    orch_dir, write = orch
    write(".scholar_subagent_model", _HAIKU)
    childval, _err = _apply_childenv("scholar", orch_dir)
    assert childval == _HAIKU


def test_apply_non_auditor_inherited_env_preserved(orch):
    """Escape hatch: a non-auditor inherits a pre-set env value (tier-1)."""
    orch_dir, _ = orch
    childval, _err = _apply_childenv("scholar", orch_dir, env_model="claude-sonnet-5")
    assert childval == "claude-sonnet-5"


# ── #152 review test (a): UNRESOLVED session fails CLOSED ────────────────────────

def test_apply_unresolved_session_fails_closed_over_fleet(orch):
    """Detached launch (empty session) + a fleet file -> NO export, var absent, loud warn.
    (The empty session used to make is_auditor_lane false and let the fleet flip through.)"""
    orch_dir, write = orch
    write(".fleet_subagent_model", _HAIKU)
    childval, err = _apply_childenv("", orch_dir)
    assert childval == "__ABSENT__", f"unresolved session must not export; got {childval!r}"
    assert "UNRESOLVED" in err and "closed" in err.lower(), f"must warn LOUD; stderr={err!r}"


def test_apply_unresolved_session_scrubs_inherited_env(orch):
    """Unresolved session must also SCRUB an inherited value (can't prove non-auditor)."""
    orch_dir, _ = orch
    childval, err = _apply_childenv("", orch_dir, env_model=_HAIKU)
    assert childval == "__ABSENT__"
    assert "UNRESOLVED" in err


# ── #152 review test (b) + auditor clamp: real exported env is ABSENT ─────────────

@pytest.mark.parametrize("sess", ["quality", "storefront", "cc-quality", "cc-storefront"])
def test_apply_auditor_marker_not_exported(orch, sess):
    orch_dir, write = orch
    write(f".{sess}_subagent_model", _HAIKU)
    write(f".{sess.replace('cc-', '')}_subagent_model", _HAIKU)
    childval, _err = _apply_childenv(sess, orch_dir)
    assert childval == "__ABSENT__", f"{sess} exported {childval!r} — CAI-1170 violated"


def test_apply_auditor_scrubs_inherited_env(orch):
    """THE #152 (b): auditor session + CLAUDE_CODE_SUBAGENT_MODEL pre-set in the PARENT env
    -> the child env must NOT inherit it (explicit unset), with a loud clamp warning."""
    orch_dir, _ = orch
    childval, err = _apply_childenv("quality", orch_dir, env_model=_HAIKU)
    assert childval == "__ABSENT__", f"auditor inherited {childval!r} from parent env — clamp bypassed"
    assert "1170" in err or "AUDITOR" in err, f"clamp must warn LOUD; stderr={err!r}"


def test_apply_auditor_fleet_not_exported(orch):
    orch_dir, write = orch
    write(".fleet_subagent_model", _HAIKU)
    childval, _err = _apply_childenv("storefront", orch_dir)
    assert childval == "__ABSENT__"
