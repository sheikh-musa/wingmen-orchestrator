"""Tests for spec_generator.validate_spec()."""

from spec_generator import validate_spec


def _make_spec(job_id=1, sections=None, promise=True, base_ref=True):
    """Build a spec string with the given sections."""
    if sections is None:
        sections = ["### Role", "### Task", "### Implementation Plan",
                     "### Acceptance Criteria", "### Files to Touch"]
    parts = []
    for s in sections:
        parts.append(f"{s}\nSome content here.\n")
    if promise:
        parts.append(f"<promise>JOB_{job_id}_DONE</promise>")
    if base_ref:
        # CAI-RESP-358: every valid spec carries a pinned base ref
        parts.append("### Base Ref (pinned — CAI-RESP-358)\n"
                     f"Pinned at spec time: origin/main @ `{'c' * 40}`.\n")
    return "\n".join(parts)


def test_valid_spec_passes():
    spec = _make_spec(job_id=1)
    ok, errs = validate_spec(spec, 1)
    assert ok
    assert errs == []


def test_missing_role_fails():
    spec = _make_spec(sections=["### Task", "### Implementation Plan",
                                "### Acceptance Criteria", "### Files to Touch"])
    ok, errs = validate_spec(spec, 1)
    assert not ok
    assert any("Role" in e for e in errs)


def test_missing_promise_fails():
    spec = _make_spec(promise=False)
    ok, errs = validate_spec(spec, 1)
    assert not ok
    assert any("promise" in e.lower() for e in errs)


def test_wrong_job_id_in_promise_fails():
    spec = _make_spec(job_id=99)
    ok, errs = validate_spec(spec, 1)
    assert not ok
    assert any("promise" in e.lower() for e in errs)


def test_case_insensitive_headings():
    spec = "### role\nfoo\n### task\nbar\n### constraints\nbaz\n### acceptance criteria\nqux\n### files to touch\n| f |\n<promise>JOB_5_DONE</promise>"
    ok, errs = validate_spec(spec, 5)
    assert ok
    assert errs == []
