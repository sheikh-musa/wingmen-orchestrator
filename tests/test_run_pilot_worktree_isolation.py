"""Tests for the `.loop-task/` isolation contract in scripts/loop/run_pilot.py
(op#35690, docs/loop-pilot-design-v1.md section 1).

A builder/reviewer role may run an untrusted/adversarial model. The worktree it
is handed must contain ONLY `.loop-task/task_id.txt` and `.loop-task/brief.md`
on top of the repo's own checked-out files: no orchestrator-checkout path hint
(no `orch_dir.txt`), and nothing from the grader's `hidden/` tree (seeded-bugs
/ grader.py). These tests lock that in for the two functions that enforce it:
`write_task_files()` and `make_worktree()`.

All git repos below are throwaway `tmp_path` repos -- nothing here touches this
repository's own `.git` or any remote.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "scripts" / "loop" / "run_pilot.py"
spec = importlib.util.spec_from_file_location("run_pilot", MODULE_PATH)
run_pilot = importlib.util.module_from_spec(spec)
sys.modules["run_pilot"] = run_pilot
spec.loader.exec_module(run_pilot)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def _make_git_repo(path: Path) -> str:
    """Initialise a bare-minimum throwaway git repo with one commit; return its HEAD."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("initial\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def test_write_task_files_creates_exactly_task_id_and_brief(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    task_id = "synthetic-task-001"
    brief = "# Brief\n\nFix the widget so it stops wobbling.\n"

    run_pilot.write_task_files(worktree, task_id, brief)

    task_dir = worktree / ".loop-task"
    assert task_dir.is_dir()
    # The ENTIRE contents of .loop-task/: these two files and nothing else.
    assert sorted(p.name for p in task_dir.iterdir()) == ["brief.md", "task_id.txt"]
    assert (task_dir / "task_id.txt").read_text() == task_id + "\n"
    assert (task_dir / "brief.md").read_text() == brief
    # Nothing else is created at the worktree root besides the .loop-task dir.
    assert sorted(p.name for p in worktree.iterdir()) == [".loop-task"]


def test_written_task_files_do_not_leak_the_orchestrator_checkout_path(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    task_id = "synthetic-task-002"
    brief = "# Brief\n\nWork only within this worktree.\n"
    # Precondition: the brief itself must not contain the sentinel, otherwise the
    # assertion below would be vacuous.
    assert str(run_pilot.ORCH_DIR) not in brief

    run_pilot.write_task_files(worktree, task_id, brief)

    task_dir = worktree / ".loop-task"
    for name in ("task_id.txt", "brief.md"):
        content = (task_dir / name).read_text()
        assert str(run_pilot.ORCH_DIR) not in content, (
            f"{name} leaks the orchestrator checkout path -- a role could then "
            f"walk to tasks/loop-pilot/<task_id>/hidden/"
        )
        # No path hint to the grader's hidden tree either.
        assert "tasks/loop-pilot" not in content
        assert "hidden" not in content


def test_make_worktree_creates_a_valid_worktree_free_of_tasks_and_hidden_dirs(tmp_path):
    repo = tmp_path / "source-repo"
    base_sha = _make_git_repo(repo)
    wt = tmp_path / "worktrees" / "task-wt"

    run_pilot.make_worktree(str(repo), wt, branch="loop-pilot/test-task", base="main")

    assert wt.is_dir(), f"worktree was not created at {wt}"
    assert (wt / "README.md").exists(), "worktree should contain the repo's checked-out files"
    # Valid git worktree: its HEAD resolves and matches the base commit.
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == base_sha
    # The named branch was created in the source repo.
    assert "loop-pilot/test-task" in _git(repo, "branch", "--list",
                                          "loop-pilot/test-task").stdout

    # Nowhere under the worktree may there be a `tasks/` or `hidden/` directory
    # (the grader's seeded-bugs.json / grader.py must never be reachable here).
    forbidden = sorted(
        str(p.relative_to(wt)) for p in wt.rglob("*")
        if p.is_dir() and p.name in {"tasks", "hidden"}
    )
    assert forbidden == [], f"worktree exposes grader directories: {forbidden}"


def test_make_worktree_detach_checks_out_base_without_creating_a_branch(tmp_path):
    repo = tmp_path / "source-repo"
    base_sha = _make_git_repo(repo)
    wt = tmp_path / "worktrees" / "detached-wt"

    # branch="" is what the reviewer path passes in run_task().
    run_pilot.make_worktree(str(repo), wt, branch="", base="main", detach=True)

    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == base_sha
    # A detached worktree has no symbolic HEAD branch.
    assert _git(wt, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0


def test_builder_visible_listing_is_repo_files_plus_the_loop_task_contract(tmp_path):
    """End to end for the contract: a real worktree + write_task_files leaves the
    builder exactly the repo's own files and .loop-task/{task_id.txt,brief.md}."""
    repo = tmp_path / "source-repo"
    _make_git_repo(repo)
    wt = tmp_path / "worktrees" / "task-wt"
    run_pilot.make_worktree(str(repo), wt, branch="loop-pilot/test-task", base="main")

    task_id = "synthetic-task-004"
    brief = "# Brief\n\nMake the change and leave the rest alone.\n"
    run_pilot.write_task_files(wt, task_id, brief)

    files = sorted(
        str(p.relative_to(wt)) for p in wt.rglob("*")
        if p.is_file() and p.relative_to(wt) != Path(".git")
    )
    assert files == [
        ".loop-task/brief.md",
        ".loop-task/task_id.txt",
        "README.md",
    ], f"unexpected builder-visible file set: {files}"

    # Content round-trips exactly, and nothing leaks the orchestrator path.
    assert (wt / ".loop-task" / "task_id.txt").read_text() == task_id + "\n"
    assert (wt / ".loop-task" / "brief.md").read_text() == brief
    for rel in files:
        assert str(run_pilot.ORCH_DIR) not in (wt / rel).read_text()

    forbidden = sorted(
        str(p.relative_to(wt)) for p in wt.rglob("*")
        if p.is_dir() and p.name in {"tasks", "hidden"}
    )
    assert forbidden == [], f"worktree exposes grader directories: {forbidden}"
