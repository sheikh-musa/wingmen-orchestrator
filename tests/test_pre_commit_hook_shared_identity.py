"""The pre-commit guard against a SHARED-config git identity in a linked worktree.

Why this exists (orch-console #81957 -> #81978): every worktree of this repo
inherits ``<common>/.git/config``, so a lane that runs a plain
``git config user.name`` inside its own linked worktree sets the identity for
the WHOLE repo. That happened on 2026-10-29: a lane set its own name, and three
other lanes' commits were then authored as it (14c44f1, fecd908, 5e9c9bc on
``fable/substrate-safe-fixes``). Nothing was lost, but the author line lied.
Console's ruling: refuse the commit instead of relying on a documented rule, so
the control does not depend on any agent remembering it.

The hook under test is the tracked ``scripts/git-hooks/pre-commit`` (installed
fleet-wide through ``core.hooksPath``). These tests copy that exact file into a
throwaway hooks directory and drive it with real ``git`` in a scratch repo, so
they exercise the shipped artifact rather than a reimplementation.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "pre-commit"


def _git(cwd, *args, check=True, env=None):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=check, capture_output=True, text=True, env=env
    )


def _hook_env(tmp_path):
    """Isolate HOME so the machine's real ~/.gitconfig cannot decide a result."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.pop("GIT_AUTHOR_NAME", None)
    env.pop("GIT_AUTHOR_EMAIL", None)
    env.pop("GIT_COMMITTER_NAME", None)
    env.pop("GIT_COMMITTER_EMAIL", None)
    return env


@pytest.fixture
def scratch(tmp_path):
    """A scratch repo with the real hook installed via core.hooksPath."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy2(HOOK, hooks / "pre-commit")
    (hooks / "pre-commit").chmod(0o755)

    repo = tmp_path / "repo"
    repo.mkdir()
    env = _hook_env(tmp_path)
    _git(repo, "init", "-q", "-b", "main", env=env)
    _git(repo, "config", "core.hooksPath", str(hooks), env=env)
    (repo / "README").write_text("scratch\n")
    _git(repo, "add", "README", env=env)
    # Baseline commit: per-command identity, never written to any config file.
    _git(
        repo,
        "-c", "user.name=setup",
        "-c", "user.email=setup@example.invalid",
        "commit", "-q", "-m", "init",
        env=env,
    )
    return repo, env


def _linked_worktree(repo, env):
    wt = repo.parent / "wt"
    _git(repo, "worktree", "add", "-q", str(wt), "-b", "lane/scratch", env=env)
    return wt


def _stage_commit(wt, env):
    (wt / "work.txt").write_text("work\n")
    _git(wt, "add", "work.txt", env=env)
    return _git(
        wt, "commit", "-m", "scratch commit", check=False, env=env
    )


def test_linked_worktree_commit_refused_when_identity_comes_from_shared_config(scratch):
    """The leak itself: identity set with a plain `git config` in a linked worktree."""
    repo, env = scratch
    _git(repo, "config", "user.name", "leaky-lane", env=env)
    _git(repo, "config", "user.email", "leaky-lane@wingmen.dev", env=env)

    wt = _linked_worktree(repo, env)
    result = _stage_commit(wt, env)

    assert result.returncode != 0, (
        "the commit must be refused: the identity it would author is in the "
        "SHARED config, which every other worktree inherits\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "shared config" in result.stderr.lower()
    assert "leaky-lane" in result.stderr
    # The one-line fix hint console asked for.
    assert "git config --worktree" in result.stderr
    assert "extensions.worktreeConfig" in result.stderr


def test_linked_worktree_commit_passes_when_identity_is_worktree_scoped(scratch):
    """The documented fix: a worktree-scoped identity overrides the shared one."""
    repo, env = scratch
    _git(repo, "config", "user.name", "leaky-lane", env=env)
    _git(repo, "config", "user.email", "leaky-lane@wingmen.dev", env=env)

    wt = _linked_worktree(repo, env)
    _git(repo, "config", "extensions.worktreeConfig", "true", env=env)
    _git(wt, "config", "--worktree", "user.name", "this-lane", env=env)
    _git(wt, "config", "--worktree", "user.email", "this-lane@wingmen.dev", env=env)

    result = _stage_commit(wt, env)

    assert result.returncode == 0, result.stderr
    ident = _git(wt, "log", "-1", "--format=%an <%ae>", env=env).stdout.strip()
    assert ident == "this-lane <this-lane@wingmen.dev>"


def test_linked_worktree_commit_passes_when_identity_is_global_only(scratch, tmp_path):
    """A global (~/.gitconfig) identity is NOT the leak -- it authors nobody's work wrongly."""
    repo, env = scratch
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text(
        "[user]\n\tname = host-user\n\temail = host-user@example.invalid\n"
    )

    wt = _linked_worktree(repo, env)
    result = _stage_commit(wt, env)

    assert result.returncode == 0, result.stderr
    ident = _git(wt, "log", "-1", "--format=%an <%ae>", env=env).stdout.strip()
    assert ident == "host-user <host-user@example.invalid>"


def test_main_checkout_commit_passes_with_shared_identity(scratch):
    """Scope boundary: the guard is for LINKED worktrees only (console #81978).

    In the main checkout the shared config IS the checkout's own config, and the
    operator/console sets identity there deliberately. Refusing there would block
    legitimate console commits for a leak that only bites other worktrees.
    """
    repo, env = scratch
    _git(repo, "config", "user.name", "console-body", env=env)
    _git(repo, "config", "user.email", "console-body@wingmen.dev", env=env)

    (repo / "work.txt").write_text("work\n")
    _git(repo, "add", "work.txt", env=env)
    result = _git(repo, "commit", "-m", "scratch commit", check=False, env=env)

    assert result.returncode == 0, result.stderr


def test_per_command_identity_in_linked_worktree_passes(scratch):
    """`git -c user.name=... commit` never writes a config file, so it stays allowed."""
    repo, env = scratch
    _git(repo, "config", "user.name", "leaky-lane", env=env)
    _git(repo, "config", "user.email", "leaky-lane@wingmen.dev", env=env)

    wt = _linked_worktree(repo, env)
    (wt / "work.txt").write_text("work\n")
    _git(wt, "add", "work.txt", env=env)
    result = _git(
        wt,
        "-c", "user.name=this-lane",
        "-c", "user.email=this-lane@wingmen.dev",
        "commit", "-m", "scratch commit",
        check=False, env=env,
    )

    assert result.returncode == 0, result.stderr
    ident = _git(wt, "log", "-1", "--format=%an <%ae>", env=env).stdout.strip()
    assert ident == "this-lane <this-lane@wingmen.dev>"
