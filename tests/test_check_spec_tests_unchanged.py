"""Tests for scripts/loop/check_spec_tests_unchanged.py (op#35690).

The check is the "spec-tests-unchanged" merge gate: a builder may implement,
but every acceptance test file the spec-writer committed must still hash to
the value recorded in `.loop/<task_id>/manifest.json` AS IT EXISTED AT THE
SPEC COMMIT. Every test here builds a real, throwaway git repo in tmp_path and
drives the actual `git show <rev>:<path>` path -- no mocking of subprocess --
so a test can only pass if genuine blob-at-revision behavior works.

No network, no DB, and nothing outside tmp_path is touched.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

_LOOP_DIR = Path(__file__).resolve().parent.parent / "scripts" / "loop"
sys.path.insert(0, str(_LOOP_DIR))

import check_spec_tests_unchanged as gate  # noqa: E402

TASK_ID = "task-001"
MANIFEST_REL = f".loop/{TASK_ID}/manifest.json"
TEST_PATH = "tests/test_acceptance.py"
TEST_CONTENT = "def test_acceptance():\n    assert 1 + 1 == 2\n"


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _manifest(*entries: tuple[str, str]) -> dict:
    return {"acceptance_tests": [{"path": p, "sha256": s} for p, s in entries]}


def _commit_manifest(repo: Path, *entries: tuple[str, str], msg: str) -> str:
    _write(repo, MANIFEST_REL, json.dumps(_manifest(*entries), indent=2) + "\n")
    return _commit(repo, msg)


@pytest.fixture
def spec_repo(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway repo whose first commit ("the spec commit") records the
    acceptance test file's hash in the manifest."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, TEST_PATH, TEST_CONTENT)
    spec_sha = _commit_manifest(
        repo, (TEST_PATH, _sha256(TEST_CONTENT)), msg="spec: acceptance test + manifest"
    )
    return repo, spec_sha


def test_edited_acceptance_test_after_spec_commit_fails(spec_repo):
    """The gap this gate exists to close: the builder changes even one byte of
    the acceptance test after the spec commit."""
    repo, spec_sha = spec_repo
    _write(repo, TEST_PATH, TEST_CONTENT + " ")
    head_sha = _commit(repo, "builder edits the acceptance test")

    ok, detail = gate.check(str(repo), TASK_ID, spec_sha, head_sha)

    assert ok is False
    assert TEST_PATH in detail
    assert "hash changed since spec commit" in detail


def test_no_change_between_spec_commit_and_head_passes(spec_repo):
    """Same tree at spec and head (e.g. the trivial case) must not false-positive."""
    repo, spec_sha = spec_repo

    ok, detail = gate.check(str(repo), TASK_ID, spec_sha, spec_sha)

    assert ok is True
    assert "1 acceptance test file(s) unchanged" in detail


def test_unrelated_change_after_spec_commit_passes(spec_repo):
    """A normal builder implementation commit that leaves the test alone passes."""
    repo, spec_sha = spec_repo
    _write(repo, "src/feature.py", "def implemented():\n    return True\n")
    head_sha = _commit(repo, "builder implements the feature")

    ok, detail = gate.check(str(repo), TASK_ID, spec_sha, head_sha)

    assert ok is True
    assert "1 acceptance test file(s) unchanged" in detail


def test_missing_acceptance_test_at_head_is_reported_as_missing(spec_repo):
    """A deleted acceptance test is a failure, and says 'missing' -- not a
    hash mismatch, which would misreport what happened."""
    repo, spec_sha = spec_repo
    _git(repo, "rm", "-q", TEST_PATH)
    head_sha = _commit(repo, "builder deletes the acceptance test")

    ok, detail = gate.check(str(repo), TASK_ID, spec_sha, head_sha)

    assert ok is False
    assert f"{TEST_PATH}: missing at head" in detail
    assert "hash changed" not in detail


def test_manifest_rewrite_at_head_cannot_launder_an_edited_test(spec_repo):
    """Anti-tamper property: the builder edits the test AND rewrites the
    manifest at head to record the new hash. check() must read the manifest at
    the SPEC commit (still carrying the original hash), so it still fails."""
    repo, spec_sha = spec_repo
    edited = TEST_CONTENT + "# builder's edit\n"
    _write(repo, TEST_PATH, edited)
    head_sha = _commit_manifest(
        repo, (TEST_PATH, _sha256(edited)), msg="builder edits test and rewrites manifest"
    )

    # Prove the laundering actually happened at HEAD: its manifest agrees with
    # the edited file, so anything reading the manifest at HEAD would pass it.
    head_manifest = json.loads(_git(repo, "show", f"{head_sha}:{MANIFEST_REL}"))
    assert head_manifest["acceptance_tests"][0]["sha256"] == _sha256(edited)

    # check() reads the manifest at spec_sha instead -> original hash -> mismatch.
    ok, detail = gate.check(str(repo), TASK_ID, spec_sha, head_sha)

    assert ok is False
    assert TEST_PATH in detail
    assert "hash changed since spec commit" in detail


def test_only_the_changed_file_is_named(spec_repo):
    """With several acceptance tests, an unchanged one is not dragged into the
    mismatch message."""
    repo, spec_sha = spec_repo
    other_path = "tests/test_other_acceptance.py"
    other_content = "def test_other():\n    assert True\n"
    _write(repo, other_path, other_content)
    spec_sha = _commit_manifest(
        repo,
        (TEST_PATH, _sha256(TEST_CONTENT)),
        (other_path, _sha256(other_content)),
        msg="spec: add a second acceptance test",
    )

    _write(repo, TEST_PATH, TEST_CONTENT + " ")
    head_sha = _commit(repo, "builder edits only one acceptance test")

    ok, detail = gate.check(str(repo), TASK_ID, spec_sha, head_sha)

    assert ok is False
    assert TEST_PATH in detail
    assert other_path not in detail


def test_missing_manifest_at_spec_commit_raises(spec_repo):
    """An absent spec-commit manifest is an error, never a silent pass."""
    repo, spec_sha = spec_repo
    with pytest.raises(FileNotFoundError, match="no-such-task/manifest.json"):
        gate.check(str(repo), "no-such-task", spec_sha, spec_sha)


def test_cli_exit_code_and_output_on_pass(spec_repo, capsys):
    repo, spec_sha = spec_repo
    rc = gate.main(["--repo-dir", str(repo), "--task-id", TASK_ID,
                    "--spec-commit-sha", spec_sha, "--head-sha", spec_sha])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("PASS:")


def test_cli_exit_code_and_output_on_fail(spec_repo, capsys):
    repo, spec_sha = spec_repo
    _write(repo, TEST_PATH, TEST_CONTENT + " ")
    head_sha = _commit(repo, "builder edits the acceptance test")
    rc = gate.main(["--repo-dir", str(repo), "--task-id", TASK_ID,
                    "--spec-commit-sha", spec_sha, "--head-sha", head_sha])
    out = capsys.readouterr().out
    assert rc == 1
    assert out.startswith("FAIL:")
