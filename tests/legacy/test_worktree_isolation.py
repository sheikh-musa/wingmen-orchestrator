"""Tests for BUG-019: git worktree isolation in ralph_runner.

Verifies that CC runs in an isolated worktree so the main working tree
stays clean even when CC writes files but is interrupted before committing.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from legacy.ralph_runner import _create_worktree, _merge_and_remove_worktree

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _make_git_repo(path: Path) -> None:
    """Initialise a bare-minimum git repo at path with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True,
                   capture_output=True)


async def test_worktree_creates_separate_path(tmp_path):
    """Worktree is created at a separate path; main tree is unaffected."""
    repo = tmp_path / "repo"
    _make_git_repo(repo)

    wt_path, branch = await _create_worktree(str(repo), job_id=999)
    try:
        wt = Path(wt_path)
        assert wt.exists(), f"worktree path {wt_path} should exist"
        assert (wt / "README.md").exists(), "worktree should contain repo files"
        # Main tree is untouched — no new branch on main
        result = subprocess.run(
            ["git", "branch", "--list", branch], cwd=repo,
            capture_output=True, text=True,
        )
        assert branch in result.stdout, "worktree branch should exist in repo"
    finally:
        await _merge_and_remove_worktree(str(repo), 999, wt_path, branch)


async def test_worktree_commit_merges_to_main(tmp_path):
    """A commit made inside the worktree is fast-forward merged to main."""
    repo = tmp_path / "repo"
    _make_git_repo(repo)

    wt_path, branch = await _create_worktree(str(repo), job_id=1001)
    wt = Path(wt_path)

    # Simulate CC: create a file and commit in the worktree
    (wt / "output.txt").write_text("CC result\n")
    subprocess.run(["git", "add", "output.txt"], cwd=wt, check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: CC output [job_1001]"],
        cwd=wt, check=True, capture_output=True,
    )

    # Merge back
    await _merge_and_remove_worktree(str(repo), 1001, wt_path, branch)

    # Commit is now on main
    assert (repo / "output.txt").exists(), (
        "output.txt committed in worktree must be present on main after merge"
    )
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=repo,
        capture_output=True, text=True,
    )
    assert "job_1001" in log.stdout, "merged commit should appear on main"

    # Worktree directory is gone
    assert not Path(wt_path).exists(), "worktree directory should be removed"


async def test_main_tree_clean_after_interrupted_worktree(tmp_path):
    """If CC writes files but doesn't commit, main tree stays clean after cleanup."""
    repo = tmp_path / "repo"
    _make_git_repo(repo)

    wt_path, branch = await _create_worktree(str(repo), job_id=1002)
    wt = Path(wt_path)

    # Simulate interrupted CC: files created, not committed
    (wt / "partial.py").write_text("incomplete work\n")
    (wt / "another.txt").write_text("more files\n")
    # No git add/commit — intentionally left dirty

    # Cleanup (what happens when orchestrator is killed mid-session)
    await _merge_and_remove_worktree(str(repo), 1002, wt_path, branch)

    # Main tree is clean
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo,
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", (
        f"Main tree must be clean after worktree cleanup; got: {result.stdout!r}"
    )

    # Worktree directory is gone
    assert not Path(wt_path).exists(), "worktree directory should be removed"

    # Main repo still has its original commit, no phantom commit
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo,
        capture_output=True, text=True,
    )
    assert log.stdout.count("\n") == 1, (
        "Main should have exactly one commit (init); interrupted work must not be merged"
    )


async def test_stale_worktree_cleaned_on_create(tmp_path):
    """_create_worktree cleans up a stale worktree from a previous run."""
    repo = tmp_path / "repo"
    _make_git_repo(repo)

    # Simulate a stale worktree from a prior interrupted run
    wt_path, branch = await _create_worktree(str(repo), job_id=1003)
    # Don't clean it up — leave it stale

    # Create the same worktree again (same job_id) — should succeed without error
    wt_path2, branch2 = await _create_worktree(str(repo), job_id=1003)
    assert wt_path == wt_path2
    assert branch == branch2

    await _merge_and_remove_worktree(str(repo), 1003, wt_path2, branch2)
    assert not Path(wt_path2).exists()
