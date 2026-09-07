"""CAI-RESP-358 — bug-job specs must pin an exact base ref (jobs 185/187 proved the gap).

Covers: resolution against a real repo, the refuse-to-emit path, the deterministic
section format, and both validate_spec arms (missing section / section without sha).
"""
import pathlib
import re

import pytest

import legacy.spec_generator as spec_generator

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")


REPO = str(pathlib.Path(__file__).resolve().parent.parent)  # this repo has an origin


def test_resolve_base_ref_returns_branch_and_full_sha():
    branch, sha = spec_generator._resolve_base_ref(REPO)
    assert branch  # e.g. 'main'
    assert re.fullmatch(r"[0-9a-f]{40}", sha)


def test_resolve_base_ref_refuses_non_repo(tmp_path):
    with pytest.raises(RuntimeError, match="refusing to emit an unpinned spec"):
        spec_generator._resolve_base_ref(str(tmp_path))


def test_base_ref_section_format_matches_validator():
    section = spec_generator._base_ref_section("main", "a" * 40)
    assert "### Base Ref (pinned — CAI-RESP-358)" in section
    assert f"git checkout -b <your-job-branch> {'a' * 40}" in section
    assert re.search(r"origin/\S+ @ `[0-9a-f]{40}`", section)
    assert "never silently rebase" in section


def _minimal_valid_spec(job_id: int, with_base: bool = True, sha: str = "b" * 40) -> str:
    spec = (
        "### Role\nx\n### Task\nx\n### Constraints\nx\n### Implementation Plan\nx\n"
        "### Acceptance Criteria\n1. x\n### Files to Touch\n| f | a | c |\n"
        f"<promise>JOB_{job_id}_DONE</promise>"
    )
    if with_base:
        spec += spec_generator._base_ref_section("main", sha)
    return spec


def test_validate_rejects_spec_without_base_ref():
    ok, errors = spec_generator.validate_spec(_minimal_valid_spec(1, with_base=False), 1)
    assert not ok
    assert any("Base Ref" in e for e in errors)


def test_validate_rejects_base_ref_without_sha():
    spec = _minimal_valid_spec(2, with_base=False) + "\n### Base Ref (pinned — CAI-RESP-358)\nno sha here\n"
    ok, errors = spec_generator.validate_spec(spec, 2)
    assert not ok
    assert any("40-hex" in e for e in errors)


def test_validate_accepts_pinned_spec():
    ok, errors = spec_generator.validate_spec(_minimal_valid_spec(3), 3)
    assert ok, errors
