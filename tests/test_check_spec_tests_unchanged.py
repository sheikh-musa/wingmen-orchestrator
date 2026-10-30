"""Tests for scripts/loop/check_spec_tests_unchanged.py — the "spec-tests-unchanged"
merge-gate check of the cc-loop 3-role loop (op#35690).

The property under test: a builder role may implement code but must NOT edit the
acceptance test files the spec-writer committed. check() enforces this by reading the
manifest AS COMMITTED AT the spec commit (never trusting the manifest at HEAD, so a
builder can't launder an edited test by also rewriting the manifest's hashes), then
recomputing each listed test file's sha256 at the PR head.

Every test builds a REAL throwaway local git repo in tmp_path — no mocks of
subprocess/git, no network, never touches this repo's own .git — so the genuine
git-blob-at-revision behaviour (git show <rev>:<path>) is actually exercised.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "loop"))

from check_spec_tests_unchanged import check  # noqa: E402


def _git(repo: Path, *args) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r.stdout.strip()


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, "--allow-empty")
    return _git(repo, "rev-parse", "HEAD")


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _manifest(task_id: str, entries: list[tuple[str, str]]) -> str:
    """Render a manifest.json with one acceptance_tests entry per (path, sha256)."""
    return json.dumps(
        {
            "task_id": task_id,
            "acceptance_tests": [{"path": p, "sha256": s} for p, s in entries],
        },
        indent=2,
    )


# ── scenario 1: the real gap this check exists to catch ──────────────────────
def test_edited_test_file_fails(tmp_path):
    """An acceptance test file edited (even one byte) after the spec commit makes the
    manifest's recorded hash stale — check() must return False and name the path."""
    repo = _init_repo(tmp_path)
    task_id = "t1"
    test_rel = "tests/test_acceptance.py"
    original = "def test_feature():\n    assert add(2, 2) == 4\n"

    _write(repo, test_rel, original)
    _write(repo, f".loop/{task_id}/manifest.json",
           _manifest(task_id, [(test_rel, _sha256(original))]))
    spec_sha = _commit(repo, "spec: acceptance tests + manifest")

    # builder edits the acceptance test by one byte (4 -> 5)
    _write(repo, test_rel, "def test_feature():\n    assert add(2, 2) == 5\n")
    head_sha = _commit(repo, "builder: weaken the acceptance test")

    ok, detail = check(str(repo), task_id, spec_sha, head_sha)
    assert ok is False
    assert test_rel in detail
    assert "hash changed" in detail


# ── scenario 2: the case this check must NOT false-positive on ───────────────
def test_unchanged_same_tree_passes(tmp_path):
    """Nothing changes between spec commit and head (same tree) — check() must pass."""
    repo = _init_repo(tmp_path)
    task_id = "t2"
    test_rel = "tests/test_acceptance.py"
    content = "def test_feature():\n    assert add(2, 2) == 4\n"

    _write(repo, test_rel, content)
    _write(repo, f".loop/{task_id}/manifest.json",
           _manifest(task_id, [(test_rel, _sha256(content))]))
    spec_sha = _commit(repo, "spec: acceptance tests + manifest")

    ok, detail = check(str(repo), task_id, spec_sha, spec_sha)
    assert ok is True
    assert "1 acceptance test file(s) unchanged" in detail


def test_unrelated_file_added_later_passes(tmp_path):
    """A later commit that adds an UNRELATED file (leaving the acceptance test byte-for-byte
    identical) must not trip the gate — the builder is allowed to add implementation."""
    repo = _init_repo(tmp_path)
    task_id = "t3"
    test_rel = "tests/test_acceptance.py"
    content = "def test_feature():\n    assert add(2, 2) == 4\n"

    _write(repo, test_rel, content)
    _write(repo, f".loop/{task_id}/manifest.json",
           _manifest(task_id, [(test_rel, _sha256(content))]))
    spec_sha = _commit(repo, "spec: acceptance tests + manifest")

    _write(repo, "src/feature.py", "def add(a, b):\n    return a + b\n")
    head_sha = _commit(repo, "builder: implement feature (does not touch the test)")

    ok, detail = check(str(repo), task_id, spec_sha, head_sha)
    assert ok is True
    assert "1 acceptance test file(s) unchanged" in detail


# ── scenario 3: a missing file at head ───────────────────────────────────────
def test_deleted_test_file_reports_missing(tmp_path):
    """The acceptance test file deleted in a later commit — check() must return False with a
    'missing' message, NOT a hash-mismatch message."""
    repo = _init_repo(tmp_path)
    task_id = "t4"
    test_rel = "tests/test_acceptance.py"
    content = "def test_feature():\n    assert add(2, 2) == 4\n"

    _write(repo, test_rel, content)
    _write(repo, f".loop/{task_id}/manifest.json",
           _manifest(task_id, [(test_rel, _sha256(content))]))
    spec_sha = _commit(repo, "spec: acceptance tests + manifest")

    (repo / test_rel).unlink()
    head_sha = _commit(repo, "builder: delete the acceptance test")

    ok, detail = check(str(repo), task_id, spec_sha, head_sha)
    assert ok is False
    assert test_rel in detail
    assert "missing at head" in detail
    assert "hash changed" not in detail


# ── scenario 4: the anti-tamper property itself ──────────────────────────────
def test_laundered_manifest_at_head_still_caught(tmp_path):
    """Builder edits the acceptance test AND rewrites the manifest at HEAD to match the new
    hash. Because check() ALWAYS reads the manifest at the spec commit (never at head), the
    laundering is invisible to it: the recomputed head hash is compared to the ORIGINAL
    (spec-commit) manifest hash, so the edit is still caught as a mismatch."""
    repo = _init_repo(tmp_path)
    task_id = "t5"
    test_rel = "tests/test_acceptance.py"
    original = "def test_feature():\n    assert add(2, 2) == 4\n"
    manifest_rel = f".loop/{task_id}/manifest.json"

    _write(repo, test_rel, original)
    _write(repo, manifest_rel, _manifest(task_id, [(test_rel, _sha256(original))]))
    spec_sha = _commit(repo, "spec: acceptance tests + manifest")

    # builder weakens the test AND rewrites the manifest hash to match the new content,
    # so at HEAD the manifest "agrees" with the file — only the spec-commit manifest betrays it.
    tampered = "def test_feature():\n    assert add(2, 2) == 5\n"
    _write(repo, test_rel, tampered)
    _write(repo, manifest_rel, _manifest(task_id, [(test_rel, _sha256(tampered))]))
    head_sha = _commit(repo, "builder: launder edited test via manifest rewrite")

    # Sanity: at HEAD the manifest really does match the file (the laundering "works"
    # against anyone naive enough to read the manifest at head).
    head_manifest = json.loads(
        subprocess.run(
            ["git", "-C", str(repo), "show", f"{head_sha}:{manifest_rel}"],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    assert head_manifest["acceptance_tests"][0]["sha256"] == _sha256(tampered)

    # But check() reads the manifest at the spec commit, so it still catches the edit.
    ok, detail = check(str(repo), task_id, spec_sha, head_sha)
    assert ok is False
    assert test_rel in detail
    assert "hash changed" in detail


# ── a couple of extra edges exercising git_show / manifest_at indirectly ─────
def test_multiple_files_one_changed_reports_only_that_one(tmp_path):
    """Two acceptance test files, only one edited: check() fails and the detail names the
    changed file but not the unchanged one."""
    repo = _init_repo(tmp_path)
    task_id = "t6"
    a_rel, b_rel = "tests/test_a.py", "tests/test_b.py"
    a, b = "def test_a():\n    assert True\n", "def test_b():\n    assert True\n"

    _write(repo, a_rel, a)
    _write(repo, b_rel, b)
    _write(repo, f".loop/{task_id}/manifest.json",
           _manifest(task_id, [(a_rel, _sha256(a)), (b_rel, _sha256(b))]))
    spec_sha = _commit(repo, "spec: two acceptance tests + manifest")

    _write(repo, b_rel, "def test_b():\n    assert False\n")
    head_sha = _commit(repo, "builder: edit only test_b")

    ok, detail = check(str(repo), task_id, spec_sha, head_sha)
    assert ok is False
    assert b_rel in detail
    assert a_rel not in detail


def test_missing_manifest_at_spec_commit_raises(tmp_path):
    """No manifest at the spec commit — manifest_at() (via check()) raises FileNotFoundError
    rather than silently passing."""
    import pytest

    repo = _init_repo(tmp_path)
    task_id = "t7"
    _write(repo, "src/feature.py", "x = 1\n")
    spec_sha = _commit(repo, "no manifest here")

    with pytest.raises(FileNotFoundError):
        check(str(repo), task_id, spec_sha, spec_sha)
