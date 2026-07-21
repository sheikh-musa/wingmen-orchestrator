"""Tests for the git-derived evidence gatherer (Head of Quality, Phase 2b).

Phase 2b is the SHADOW-mode, read-only companion to the Phase 2 evaluator:
`quality_gate_evidence.gather(cwd)` inspects a repo working dir with plain `git`
reads and returns the `{sha, checks}` shape the evaluator consumes — but ONLY
for checks whose semantics are UNAMBIGUOUS from git alone:

  - committed-on-branch      (G10): clean tree AND on a named branch
  - migrations-tracked       (G8):  nothing untracked/modified under migrations/
  - ancestor-of-origin-main  (G7):  origin/main is an ancestor of HEAD

Everything else (CI status, secret/PII scan, deploy provenance, i18n,
screenshots, reviews) is DELIBERATELY absent — the honest dead-man default: an
un-gatherable floor check must surface as `unproven` at the evaluator, never be
faked to a pass. These tests init throwaway git repos in tmp_path and assert the
derived checks against real git behaviour (no mocks).
"""

import re
import subprocess

import pytest

from nervous_system import ihsan_gate, quality_gate, quality_gate_evidence as ev


# --------------------------------------------------------------------------- #
# Helpers — real throwaway git repos in tmp_path
# --------------------------------------------------------------------------- #

def _git(cwd, *args):
    """Run a git command in cwd, raising on failure (test-setup only)."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path):
    """Init a repo on branch `main` with identity + a first commit."""
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@wingmen.local")
    _git(path, "config", "user.name", "Test Runner")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    _git(path, "branch", "-M", "main")
    return path


def _head_sha(path):
    return _git(path, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------- #
# committed-on-branch (G10)
# --------------------------------------------------------------------------- #

def test_clean_repo_on_named_branch_is_committed_on_branch(tmp_path):
    _init_repo(tmp_path)
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["committed-on-branch"] == "pass"


def test_dirty_working_tree_is_not_committed_on_branch(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("dirty change\n")  # unstaged modification
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["committed-on-branch"] == "fail"


def test_untracked_file_is_not_committed_on_branch(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "scratch.txt").write_text("untracked\n")
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["committed-on-branch"] == "fail"


def test_detached_head_is_not_committed_on_branch(tmp_path):
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    _git(tmp_path, "checkout", "-q", sha)  # detach
    checks = ev.gather(str(tmp_path))["checks"]
    # Clean tree but no named branch -> honestly a fail (still determinable).
    assert checks["committed-on-branch"] == "fail"


# --------------------------------------------------------------------------- #
# migrations-tracked (G8)
# --------------------------------------------------------------------------- #

def test_committed_migrations_are_tracked(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001_init.sql").write_text("-- init\n")
    _git(tmp_path, "add", "migrations/001_init.sql")
    _git(tmp_path, "commit", "-q", "-m", "add migration")
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["migrations-tracked"] == "pass"


def test_no_migrations_dir_is_vacuously_tracked(tmp_path):
    _init_repo(tmp_path)  # no migrations/ at all
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["migrations-tracked"] == "pass"


def test_untracked_migration_file_is_not_tracked(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "002_new.sql").write_text("-- uncommitted\n")
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["migrations-tracked"] == "fail"


def test_modified_migration_file_is_not_tracked(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "migrations").mkdir()
    mig = tmp_path / "migrations" / "003_x.sql"
    mig.write_text("-- v1\n")
    _git(tmp_path, "add", "migrations/003_x.sql")
    _git(tmp_path, "commit", "-q", "-m", "mig v1")
    mig.write_text("-- v1\n-- edited out of band\n")  # modify committed file
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["migrations-tracked"] == "fail"


# --------------------------------------------------------------------------- #
# ancestor-of-origin-main (G7)
# --------------------------------------------------------------------------- #

def test_head_at_or_ahead_of_origin_main_passes(tmp_path):
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    # Simulate a remote-tracking ref pointing at (an ancestor of) HEAD.
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", sha)
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["ancestor-of-origin-main"] == "pass"


def test_head_behind_origin_main_fails(tmp_path):
    _init_repo(tmp_path)
    base = _head_sha(tmp_path)
    (tmp_path / "b.txt").write_text("second\n")
    _git(tmp_path, "add", "b.txt")
    _git(tmp_path, "commit", "-q", "-m", "second")
    ahead = _head_sha(tmp_path)
    # origin/main is AHEAD (at `ahead`); move HEAD back to `base` which does not
    # contain `ahead` -> origin/main is NOT an ancestor of HEAD.
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", ahead)
    _git(tmp_path, "reset", "-q", "--hard", base)
    checks = ev.gather(str(tmp_path))["checks"]
    assert checks["ancestor-of-origin-main"] == "fail"


def test_no_origin_main_leaves_ancestor_check_absent(tmp_path):
    _init_repo(tmp_path)  # no remote-tracking ref
    checks = ev.gather(str(tmp_path))["checks"]
    # Cannot determine honestly -> absent, so the evaluator marks it unproven.
    assert "ancestor-of-origin-main" not in checks


# --------------------------------------------------------------------------- #
# sha + non-git + shape
# --------------------------------------------------------------------------- #

def test_sha_matches_git_rev_parse_head(tmp_path):
    _init_repo(tmp_path)
    result = ev.gather(str(tmp_path))
    assert result["sha"] == _head_sha(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{40}", result["sha"])


def test_non_git_dir_returns_empty_evidence(tmp_path):
    result = ev.gather(str(tmp_path))  # tmp_path is not a git repo
    assert result["sha"] == ""
    assert result["checks"] == {}


def test_gather_never_writes_or_networks_only_returns_shape(tmp_path):
    _init_repo(tmp_path)
    result = ev.gather(str(tmp_path))
    assert set(result.keys()) == {"sha", "checks"}
    assert isinstance(result["checks"], dict)
    # Only ever the three git-derivable checks — nothing invented.
    assert set(result["checks"]).issubset(
        {"committed-on-branch", "migrations-tracked", "ancestor-of-origin-main"}
    )


# --------------------------------------------------------------------------- #
# Integration with the evaluator: absent checks surface as unproven (honest)
# --------------------------------------------------------------------------- #

def test_gathered_evidence_feeds_evaluator_and_absent_checks_are_unproven(tmp_path):
    _init_repo(tmp_path)
    sha = _head_sha(tmp_path)
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", sha)
    evidence = ev.gather(str(tmp_path))
    verdict = quality_gate.evaluate("docs-copy", evidence)
    # docs-copy requires G1, G7, G10. We supplied G7 (ancestor) + G10
    # (committed-on-branch) but NOT G1's checks -> G1 must be unproven.
    unproven_ids = [u.id for u in verdict.unproven]
    assert "G1" in unproven_ids
    # G10's git-derived check passed, so G10 is not a failure.
    assert "G10" not in [f.id for f in verdict.failures]
