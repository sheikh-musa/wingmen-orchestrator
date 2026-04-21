"""STRONG recoverability test for BUG-019 auto-stash.

Paired with tests/test_fire_drill_unit.py::test_drill_dirty_tree_auto_stashes_and_proceeds
(WEAK — verifies the drill reaches the stash code path via mock traversal).

This test exercises the actual git stash subprocess against a real
temporary repo, then asserts that `git stash list` shows the BUG-019
marker. The point is not to re-test wingmen_orch.run_job — it's to
prove the mechanism under test (git stash push -u with a BUG-019
marker) genuinely preserves dirty work and is inspectable later.

If the stash subprocess silently fails (wrong flags, wrong cwd, etc.),
a mock-traversal test would still pass while the invariant is broken.
This test catches that.
"""
import asyncio
import subprocess
from pathlib import Path

import pytest


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def dirty_repo(tmp_path: Path) -> Path:
    """Create a tmp git repo with one committed file and one uncommitted edit."""
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)
    _run(["git", "config", "commit.gpgsign", "false"], tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("initial\n")
    _run(["git", "add", "tracked.txt"], tmp_path)
    _run(["git", "commit", "-q", "-m", "initial"], tmp_path)

    # Make the tree dirty — one modified file + one untracked file.
    tracked.write_text("modified by precursor session\n")
    (tmp_path / "untracked.txt").write_text("leftover from crash\n")
    return tmp_path


@pytest.mark.asyncio
async def test_bug019_auto_stash_preserves_work_in_real_repo(dirty_repo: Path):
    """The exact subprocess call wingmen_orch.py makes must produce a
    recoverable stash entry tagged with the BUG-019 marker."""
    job_id = 99
    stash_msg = f"BUG-019 auto-stash before job #{job_id}"

    # Mirror wingmen_orch.py:1071-1077 exactly.
    proc = await asyncio.create_subprocess_exec(
        "git", "stash", "push", "-u",
        "-m", stash_msg,
        cwd=str(dirty_repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, f"stash failed: {stderr.decode()}"

    # Tree is clean after stash.
    status = _run(["git", "status", "--porcelain"], dirty_repo)
    assert status.stdout == "", f"expected clean tree, got: {status.stdout!r}"

    # Stash is inspectable with the BUG-019 marker intact.
    stash_list = _run(["git", "stash", "list"], dirty_repo)
    assert stash_msg in stash_list.stdout, (
        f"BUG-019 marker not in `git stash list`:\n{stash_list.stdout}"
    )

    # And the work is actually recoverable — not silently dropped.
    _run(["git", "stash", "pop"], dirty_repo)
    assert (dirty_repo / "tracked.txt").read_text() == "modified by precursor session\n"
    assert (dirty_repo / "untracked.txt").read_text() == "leftover from crash\n"
