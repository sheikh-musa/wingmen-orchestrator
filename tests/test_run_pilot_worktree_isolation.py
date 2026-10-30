"""Tests for the .loop-task/ worktree-isolation contract in
scripts/loop/run_pilot.py (op#35690).

A builder role may run an untrusted/adversarial model, so the worktree it is
handed must contain ONLY the harness's plain task files
(`.loop-task/task_id.txt` + `.loop-task/brief.md`) on top of the repo's own
checked-out tree -- nothing that reveals where the orchestrator checkout
lives, and nothing from tasks/loop-pilot/<task_id>/hidden/ (the grader's
seeded-bugs.json / grader.py). See the ".loop-task/ harness contract" comment
block above write_task_files() and docs/loop-pilot-design-v1.md section 1 for
why there is no orch_dir.txt (or any path hint) written into the worktree.

These tests pin that exact file set for write_task_files() and make_worktree()
so a future change that leaks a path hint or an extra file fails loudly.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "scripts" / "loop" / "run_pilot.py"
spec = importlib.util.spec_from_file_location("run_pilot", MODULE_PATH)
run_pilot = importlib.util.module_from_spec(spec)
sys.modules["run_pilot"] = run_pilot
spec.loader.exec_module(run_pilot)


# ── helpers ──────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path, extra_files: dict[str, str] | None = None) -> None:
    """A throwaway local git repo with one commit, so there is a valid base
    ref to create a worktree from. Local-only -- never fetches or pushes."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("throwaway repo\n")
    for rel, content in (extra_files or {}).items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")


def _rel_files(root: Path) -> set[str]:
    """Every regular file under root, as posix-relative paths. A linked git
    worktree's .git is a file (not a dir), so nothing under it is walked; we
    keep the .git entry itself in the listing since it is part of what lands
    in the worktree."""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        # Do not descend into a (defensively) possible .git directory.
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            out.add((Path(dirpath) / name).relative_to(root).as_posix())
    return out


# ── write_task_files(): exact file set, no path leak ───────────────────────

def test_write_task_files_writes_exactly_two_files_and_nothing_else(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()

    run_pilot.write_task_files(worktree, "loop-pilot-007", "do the thing\n")

    # Worktree root holds only the .loop-task/ directory.
    assert [p.name for p in worktree.iterdir()] == [".loop-task"]

    task_dir = worktree / ".loop-task"
    assert sorted(p.name for p in task_dir.iterdir()) == ["brief.md", "task_id.txt"]

    assert (task_dir / "task_id.txt").read_text() == "loop-pilot-007\n"
    assert (task_dir / "brief.md").read_text() == "do the thing\n"


def test_write_task_files_leaks_no_orchestrator_path(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()

    # A brief that does NOT itself mention the orchestrator checkout path.
    brief = "Fix the failing unit test in src/widget.py. No path hints here.\n"
    run_pilot.write_task_files(worktree, "loop-pilot-008", brief)

    orch_dir = str(run_pilot.ORCH_DIR)
    task_id_content = (worktree / ".loop-task" / "task_id.txt").read_text()
    brief_content = (worktree / ".loop-task" / "brief.md").read_text()

    assert orch_dir not in task_id_content
    assert orch_dir not in brief_content
    # Brief round-trips unchanged.
    assert brief_content == brief


# ── make_worktree(): valid worktree, no grader tree ────────────────────────

def test_make_worktree_creates_valid_worktree_without_grader_tree(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    worktree = tmp_path / "wt"

    run_pilot.make_worktree(str(repo), worktree, "builder-lane", "HEAD")

    assert worktree.is_dir()
    # A real, usable git worktree.
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert len(head) == 40

    # Critically: the grader's hidden tree is nowhere under the worktree.
    for dirpath, dirnames, _filenames in os.walk(worktree):
        if ".git" in dirnames:
            dirnames.remove(".git")
        assert "tasks" not in dirnames, f"unexpected tasks/ dir at {dirpath}"
        assert "hidden" not in dirnames, f"unexpected hidden/ dir at {dirpath}"


# ── combined: the full set a builder role ever sees ────────────────────────

def test_builder_worktree_shows_only_repo_tree_plus_task_files(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, extra_files={"src/widget.py": "# widget\n"})
    worktree = tmp_path / "wt"

    run_pilot.make_worktree(str(repo), worktree, "builder-lane", "HEAD")
    run_pilot.write_task_files(worktree, "loop-pilot-009", "fix the widget\n")

    expected = {
        "README.md",          # repo's own checked-out files
        "src/widget.py",
        ".git",               # linked-worktree gitlink file
        ".loop-task/task_id.txt",
        ".loop-task/brief.md",
    }
    assert _rel_files(worktree) == expected
