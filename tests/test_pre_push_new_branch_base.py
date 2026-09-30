"""pre-push gate 2: a brand-new remote branch must not be blocked for gated content that
is IDENTICAL to the reviewed trunk (Fable audit follow-up, orch-console bus #46860, item 1).

WHY: git streams "<local_ref> <local_sha> <remote_ref> <remote_sha>" per pushed ref. For a
BRAND-NEW branch, `remote_sha` is all-zeros, and the hook used to diff the pushed commit
against the EMPTY TREE — so every gated file the branch carries (even byte-identical to the
already-reviewed trunk) looked like new unreviewed content. This blocked fleet-health's
shell-only PR #227, which touched no console file at all but happened to be a fresh branch.

FIX: for a zero remote_sha, diff against the merge-base with the reviewed trunk
(origin/fable/substrate-safe-fixes) instead of the empty tree. Content identical to trunk
is not new; content that actually changed relative to trunk is still new and still blocked.
If the trunk ref can't be resolved (no fetch, renamed remote) or shares no history with the
pushed branch, fall back to the old conservative empty-tree diff — never silently widen the
gate on an unknown base.

Builds a real temp git repo per test (git init + commits + a `refs/remotes/origin/...` ref
set directly, no network) and runs the ACTUAL hook script as a subprocess against it, feeding
the real git pre-push stdin protocol. No DB.
"""
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / "scripts" / "git-hooks" / "pre-push"
_MANIFEST_SRC = (_ROOT / "scripts" / "lib" / "console_deploy_manifest.sh").read_text()
_ZERO = "0" * 40
_TRUNK_REF = "refs/remotes/origin/fable/substrate-safe-fixes"


def _git(repo: Path, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r


def _write(repo: Path, rel: str, content: str):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, "--allow-empty")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def trunk_repo(tmp_path):
    """A repo with a 'reviewed' trunk commit (C0) carrying gated console content, and a
    refs/remotes/origin/fable/substrate-safe-fixes ref pointing at it — no network."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    # the hook sources this from the WORKING TREE at push time (git rev-parse --show-toplevel
    # of cwd) -> must be present on disk, same relative path as the real repo.
    _write(repo, "scripts/lib/console_deploy_manifest.sh", _MANIFEST_SRC)
    _write(repo, "nervous_system/console/static/index.html", "trunk-v1")
    _write(repo, "nervous_system/console/app.py", "print('v1')\n")
    c0 = _commit(repo, "trunk: reviewed console content")
    _git(repo, "update-ref", _TRUNK_REF, c0)
    return repo, c0


def _run_hook(repo: Path, stdin_line: str):
    return subprocess.run(["bash", str(_HOOK)], cwd=str(repo), input=stdin_line,
                          capture_output=True, text=True)


def test_a_new_branch_no_gated_change_is_allowed(trunk_repo):
    repo, c0 = trunk_repo
    _git(repo, "checkout", "-q", "-b", "feature/shell-only")
    _write(repo, "scripts/some_unrelated_tool.sh", "#!/usr/bin/env bash\necho hi\n")
    c1 = _commit(repo, "unrelated shell-only change")
    r = _run_hook(repo, f"refs/heads/feature/shell-only {c1} refs/heads/feature/shell-only {_ZERO}\n")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "BLOCKED" not in r.stderr


def test_b_new_branch_that_changes_gated_content_is_still_blocked(trunk_repo):
    repo, c0 = trunk_repo
    _git(repo, "checkout", "-q", "-b", "feature/console-change")
    _write(repo, "nervous_system/console/static/index.html", "trunk-v2-DIFFERENT")
    c1 = _commit(repo, "actually changes console content")
    r = _run_hook(repo, f"refs/heads/feature/console-change {c1} refs/heads/feature/console-change {_ZERO}\n")
    assert r.returncode != 0, f"should have been blocked; stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "BLOCKED" in r.stderr
    assert "no cc-quality review" in r.stderr


def test_c_existing_branch_behavior_unchanged_no_gated_change(trunk_repo):
    repo, c0 = trunk_repo
    _git(repo, "checkout", "-q", "-b", "feature/existing")
    _write(repo, "README.md", "unrelated\n")
    c1 = _commit(repo, "unrelated change on an existing branch")
    # remote already has c0 (non-zero) -> the ORIGINAL code path, unchanged
    r = _run_hook(repo, f"refs/heads/feature/existing {c1} refs/heads/feature/existing {c0}\n")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


def test_c_existing_branch_behavior_unchanged_gated_change_still_blocked(trunk_repo):
    repo, c0 = trunk_repo
    _git(repo, "checkout", "-q", "-b", "feature/existing-console-change")
    _write(repo, "nervous_system/console/static/index.html", "trunk-v3-DIFFERENT")
    c1 = _commit(repo, "console change on an existing branch")
    r = _run_hook(repo, f"refs/heads/feature/existing-console-change {c1} "
                        f"refs/heads/feature/existing-console-change {c0}\n")
    assert r.returncode != 0
    assert "BLOCKED" in r.stderr


def test_new_branch_with_review_present_is_allowed(trunk_repo):
    """A new branch that DOES carry a real console change is allowed once the review for
    that exact content hash exists — proves the fix only widens the BASE, not the gate."""
    repo, c0 = trunk_repo
    _git(repo, "checkout", "-q", "-b", "feature/reviewed-change")
    _write(repo, "nervous_system/console/static/index.html", "trunk-v4-REVIEWED")
    c1 = _commit(repo, "console change with a review")
    r = subprocess.run(
        ["bash", "-c", f'source "{repo}/scripts/lib/console_deploy_manifest.sh"; '
                       f'console_content_hash "{repo}"'],
        capture_output=True, text=True)
    h = r.stdout.strip()
    assert h, f"could not compute content hash: {r.stderr}"
    review = repo / "reports" / "console-deploy" / h / "cc-quality-review.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("reviewed, looks good\n")
    r2 = _run_hook(repo, f"refs/heads/feature/reviewed-change {c1} "
                         f"refs/heads/feature/reviewed-change {_ZERO}\n")
    assert r2.returncode == 0, f"stdout={r2.stdout!r} stderr={r2.stderr!r}"


def test_new_branch_falls_back_to_empty_tree_when_trunk_ref_unresolvable(tmp_path):
    """No origin/fable/substrate-safe-fixes ref at all (fresh clone, no fetch, renamed
    remote) -> falls back to the OLD conservative empty-tree diff, never silently widens
    the gate on an unknown base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _write(repo, "scripts/lib/console_deploy_manifest.sh", _MANIFEST_SRC)
    _write(repo, "nervous_system/console/static/index.html", "v1")
    c0 = _commit(repo, "some console content, no trunk ref exists")
    # note: NO refs/remotes/origin/fable/substrate-safe-fixes set up
    r = _run_hook(repo, f"refs/heads/x {c0} refs/heads/x {_ZERO}\n")
    assert r.returncode != 0, "unresolvable trunk ref must fall back to the safe (blocking) default"
    assert "BLOCKED" in r.stderr
