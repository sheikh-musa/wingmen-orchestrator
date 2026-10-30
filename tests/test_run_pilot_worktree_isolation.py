"""Tests for the `.loop-task/` isolation contract in scripts/loop/run_pilot.py
(op#35690, docs/loop-pilot-design-v1.md section 1).

A builder/reviewer role may run an untrusted/adversarial model. On top of the
repo's own checked-out files, the worktree must carry exactly
`.loop-task/task_id.txt` and `.loop-task/brief.md` -- no orchestrator-checkout
path hint (no `orch_dir.txt`). `test_write_task_files_*` below lock in what
`write_task_files()` actually enforces.

`make_worktree()` enforces NOTHING about the grader's `hidden/` tree: it is a
plain `git worktree add` (no exclusion logic at all). The tests below seed
`tasks/loop-pilot/<id>/hidden/seeded-bugs.json` into the base commit and assert
the honest, verified result -- `git worktree add` checks out the base tree
verbatim, so that file IS present in the role's worktree. The isolation that
matters operationally (the grader tree never reaching a role) comes from the
`tasks/loop-pilot/<task_id>/hidden/` tree never being committed into
`loop-pilot/base` in the first place, not from anything `make_worktree()` does.

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


SEEDED_TASK_ID = "synthetic-seed-001"
HIDDEN_REL = Path("tasks") / "loop-pilot" / SEEDED_TASK_ID / "hidden" / "seeded-bugs.json"
HIDDEN_CONTENT = '{"bugs": []}\n'


def _seed_hidden_tree(repo: Path) -> str:
    """Commit the grader's hidden tree into `repo`'s own history; return the new HEAD.

    Production relies on `tasks/loop-pilot/<task_id>/hidden/` NEVER being
    committed into the `loop-pilot/base` branch a role worktree is made from.
    Seeding exactly such a tree here makes the worktree assertions exercise
    something real instead of a fixture where there was nothing to find.
    """
    hidden_file = repo / HIDDEN_REL
    hidden_file.parent.mkdir(parents=True, exist_ok=True)
    hidden_file.write_text(HIDDEN_CONTENT)
    _git(repo, "add", str(HIDDEN_REL))
    _git(repo, "commit", "-m", "seed grader hidden tree")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


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


def test_make_worktree_checks_out_a_committed_hidden_tree_verbatim(tmp_path):
    """`make_worktree()` is a plain `git worktree add` with NO exclusion logic.

    This replaces a VACUOUS assertion ("the worktree has no tasks/ or hidden/
    dir") made against a fixture that never committed one -- true only because
    there was nothing to find, so it could not catch a regression.

    Here the base commit DOES contain `tasks/loop-pilot/<id>/hidden/seeded-bugs.json`.
    Verified behavior, asserted honestly: `git worktree add` checks out the whole
    base tree, so the hidden file IS present in the role's worktree.
    `make_worktree()` does not strip it, and this test fails loudly if someone
    later changes that premise without updating the story.
    """
    repo = tmp_path / "source-repo"
    _make_git_repo(repo)
    base_sha = _seed_hidden_tree(repo)
    wt = tmp_path / "worktrees" / "task-wt"

    run_pilot.make_worktree(str(repo), wt, branch="loop-pilot/test-task", base="main")

    assert wt.is_dir(), f"worktree was not created at {wt}"
    # Valid git worktree at the seeded base commit.
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == base_sha
    assert "loop-pilot/test-task" in _git(repo, "branch", "--list",
                                          "loop-pilot/test-task").stdout

    # Honest assertion: the committed grader tree is fully visible in the
    # worktree. If this ever starts failing, make_worktree() has gained real
    # isolation behavior and this test's premise (and the module docstring)
    # must be revisited.
    leaked_file = wt / HIDDEN_REL
    assert leaked_file.is_file(), (
        "expected `git worktree add` to check out the base commit verbatim, so the "
        f"seeded grader file {HIDDEN_REL} would be reachable from the role's worktree")
    assert leaked_file.read_text() == HIDDEN_CONTENT
    leaked_dirs = sorted(
        str(p.relative_to(wt)) for p in wt.rglob("*")
        if p.is_dir() and p.name in {"tasks", "hidden"}
    )
    assert leaked_dirs == [
        "tasks",
        f"tasks/loop-pilot/{SEEDED_TASK_ID}/hidden",
    ], f"unexpected set of grader-shaped directories in the worktree: {leaked_dirs}"


def test_make_worktree_from_a_base_without_a_hidden_tree_contains_none(tmp_path):
    """Weaker but true statement (the remit of the old vacuous assertion).

    When the base commit never contains `tasks/loop-pilot/<id>/hidden/` -- the
    production invariant -- the worktree contains none of those directories
    either. This says NOTHING about `make_worktree()` excluding them; it does
    not (see the companion test above, which seeds the tree and shows it is
    checked out verbatim).
    """
    repo = tmp_path / "source-repo"
    base_sha = _make_git_repo(repo)
    wt = tmp_path / "worktrees" / "task-wt"

    run_pilot.make_worktree(str(repo), wt, branch="loop-pilot/test-task", base="main")

    assert wt.is_dir(), f"worktree was not created at {wt}"
    assert (wt / "README.md").exists(), "worktree should contain the repo's checked-out files"
    # Valid git worktree: its HEAD resolves and matches the base commit.
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == base_sha

    # No `tasks/` or `hidden/` directory appears -- because the base commit had
    # none, not because make_worktree() filters anything.
    forbidden = sorted(
        str(p.relative_to(wt)) for p in wt.rglob("*")
        if p.is_dir() and p.name in {"tasks", "hidden"}
    )
    assert forbidden == [], f"unexpected grader-shaped directories: {forbidden}"


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
    builder exactly the repo's own files and .loop-task/{task_id.txt,brief.md}.

    The base commit here also seeds a grader hidden tree, so the listing is the
    honest full one: everything the role can see, including a hidden tree that
    was committed into the base -- `make_worktree()` does not remove it. In
    production the isolation comes from never committing that tree into
    `loop-pilot/base` (see the module docstring)."""
    repo = tmp_path / "source-repo"
    _make_git_repo(repo)
    _seed_hidden_tree(repo)
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
        str(HIDDEN_REL),
    ], f"unexpected builder-visible file set: {files}"

    # Content round-trips exactly, and nothing leaks the orchestrator path.
    assert (wt / ".loop-task" / "task_id.txt").read_text() == task_id + "\n"
    assert (wt / ".loop-task" / "brief.md").read_text() == brief
    for rel in files:
        assert str(run_pilot.ORCH_DIR) not in (wt / rel).read_text()

    # write_task_files() adds only .loop-task/; it does not and cannot remove
    # the seeded grader tree -- asserted above as present.
    assert (wt / HIDDEN_REL).read_text() == HIDDEN_CONTENT
