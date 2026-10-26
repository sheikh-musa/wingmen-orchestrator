"""op#29348 round 3 (2026-10-26, bus #77152/#77123): PR#514's boot-time
`unset GOOGLE_APPLICATION_CREDENTIALS` (see test_boot_scripts_strip_gac.py)
only cleans the long-lived launcher process's own env. zsh unconditionally
re-sources ~/.zshenv for EVERY invocation it spawns (interactive or `-c`,
regardless of parent env) -- so every individual Bash-tool command still
picked the operator's real secrets (GH_TOKEN, GOOGLE_APPLICATION_CREDENTIALS,
CLAUDE_CODE_OAUTH_TOKEN) straight back up off disk, invisible to
lane_prod_key_guard's ambient check (which reads the harness process's own
env, not the executed command's fresh subshell).

Dispositive isolation (the test design that pinned this down): inheritance
from an already-contaminated parent shell can masquerade as "re-sourcing"
unless the suspect var is unset in the parent FIRST. Every test below that
spawns a real zsh/bash child strips the ambient secrets from ITS OWN
environment before spawning, for exactly this reason -- the first attempt at
this diagnosis (same incident) got a false "still broken" reading by
skipping that step.

Fix: each boot launcher now exports `ZDOTDIR="$ORCH_DIR/deploy/agent-zdotdir"`
right after the GAC unset, pointing zsh at a tracked, secret-free filtered
copy of the operator's real dotfiles instead of $HOME -- so agent-spawned
shells never reach ~/.zshenv/~/.zshrc at all. This does NOT touch the
operator's own dotfiles or his own interactive shell sessions (he doesn't go
through these boot scripts).
"""
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
_AGENT_ZDOTDIR = _REPO_ROOT / "deploy" / "agent-zdotdir"

LAUNCHERS = [
    "boot_orch.sh",
    "boot_nazim.sh",
    "boot_cai.sh",
    "boot_fleet_health.sh",
    "boot_quality.sh",
]

_UNSET_RE = re.compile(r"^\s*unset\s+GOOGLE_APPLICATION_CREDENTIALS\s+GOOGLE_CLOUD_PROJECT\s*$", re.MULTILINE)
_ZDOTDIR_RE = re.compile(r'^\s*export\s+ZDOTDIR="\$ORCH_DIR/deploy/agent-zdotdir"\s*$', re.MULTILINE)
_SECRET_VARS = ("GH_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "CLAUDE_CODE_OAUTH_TOKEN")


def test_all_launchers_export_zdotdir_after_the_unset():
    missing = []
    for name in LAUNCHERS:
        path = _SCRIPTS / name
        assert path.exists(), f"launcher {name} not found at {path}"
        text = path.read_text()
        unset_match = _UNSET_RE.search(text)
        zdotdir_match = _ZDOTDIR_RE.search(text)
        if not unset_match or not zdotdir_match or zdotdir_match.start() < unset_match.start():
            missing.append(name)
    assert not missing, (
        "these launchers don't export ZDOTDIR at the agent-zdotdir path after "
        "the GAC unset, so every Bash-tool subshell they spawn still re-sources "
        "the operator's real ~/.zshenv and re-leaks the prod cred: " + ", ".join(missing)
    )


def test_agent_zdotdir_zshenv_exists_and_is_secret_free():
    zshenv = _AGENT_ZDOTDIR / ".zshenv"
    assert zshenv.exists(), f"{zshenv} must exist — it's what ZDOTDIR points every lane shell at"
    text = zshenv.read_text()
    found = [v for v in _SECRET_VARS if re.search(rf'^\s*export\s+{v}=', text, re.MULTILINE)]
    assert not found, (
        f"deploy/agent-zdotdir/.zshenv must never carry a live export for any of "
        f"{_SECRET_VARS} — found: {found}"
    )


def test_wetprove_zdotdir_blocks_sentinel_secrets_but_keeps_real_path_setup(tmp_path):
    """Wet-prove, not a grep: spawn a REAL zsh child against a FAKE $HOME whose
    .zshenv/.zshrc carry sentinel (non-secret-shaped, so the transcript guard
    never trips) stand-ins for the 4 real secret lines, with ZDOTDIR pointed at
    the REAL shipped deploy/agent-zdotdir -- and assert the sentinels never
    reach the child while JAVA_HOME (a real, needed non-secret from that file)
    does."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    (fake_home / ".zshenv").write_text(
        "export GH_TOKEN=NOTREALSENTINEL_GH\n"
        "export GOOGLE_APPLICATION_CREDENTIALS=/fake/.wingmen/keys/notrealsentinel.json\n"
    )
    (fake_home / ".zshrc").write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN=NOTREALSENTINEL_OAUTH\n"
    )

    check_script = tmp_path / "check.sh"
    check_script.write_text(
        "#!/bin/zsh\n"
        'for v in GOOGLE_APPLICATION_CREDENTIALS GH_TOKEN CLAUDE_CODE_OAUTH_TOKEN JAVA_HOME; do\n'
        '  val="${(P)v}"\n'
        '  if [ -n "$val" ]; then echo "${v}=PRESENT"; else echo "${v}=ABSENT"; fi\n'
        'done\n'
    )

    import os
    base_env = {k: v for k, v in os.environ.items() if k not in _SECRET_VARS}

    def run(extra_env):
        env = dict(base_env)
        env.update(extra_env)
        result = subprocess.run(
            ["zsh", str(check_script)], capture_output=True, text=True, timeout=10, env=env,
        )
        return result.stdout

    baseline = run({"HOME": str(fake_home)})
    assert "GOOGLE_APPLICATION_CREDENTIALS=PRESENT" in baseline, (
        "sanity check failed: the fake HOME's sentinel dotfile should leak into a "
        "plain zsh child with no ZDOTDIR override — if this fails the test fixture "
        "itself is broken, not the fix"
    )
    assert "GH_TOKEN=PRESENT" in baseline

    with_fix = run({"HOME": str(fake_home), "ZDOTDIR": str(_AGENT_ZDOTDIR)})
    assert "GOOGLE_APPLICATION_CREDENTIALS=ABSENT" in with_fix, (
        f"ZDOTDIR override did not stop the sentinel GAC from reaching the child: {with_fix!r}"
    )
    assert "GH_TOKEN=ABSENT" in with_fix, f"ZDOTDIR override did not stop the sentinel GH_TOKEN: {with_fix!r}"
    assert "CLAUDE_CODE_OAUTH_TOKEN=ABSENT" in with_fix, f"{with_fix!r}"
    assert "JAVA_HOME=PRESENT" in with_fix, (
        f"ZDOTDIR override dropped a real, needed non-secret (JAVA_HOME) that lanes "
        f"rely on — the filtered dotfile must carry this forward: {with_fix!r}"
    )
