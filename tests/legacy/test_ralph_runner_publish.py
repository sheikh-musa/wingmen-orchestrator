"""Tests for the ralph_runner publish_job_commit helper.

This helper is called from wingmen_orch after ARCH-021 gates pass.
Keeps the merge+cleanup in _merge_and_remove_worktree isolated;
publishing only happens once the commit has survived the gates.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



@pytest.mark.asyncio
async def test_publish_job_commit_constructs_canonical_branch_name():
    from legacy.ralph_runner import publish_job_commit

    rev_parse_proc = MagicMock()
    rev_parse_proc.returncode = 0
    rev_parse_proc.communicate = AsyncMock(return_value=(
        b"14ae9556e73dd6384f3c5f6e9b4333a7cf4deed9\n", b"",
    ))
    branch_proc = MagicMock()
    branch_proc.returncode = 0
    branch_proc.communicate = AsyncMock(return_value=(b"", b""))
    push_proc = MagicMock()
    push_proc.returncode = 0
    push_proc.communicate = AsyncMock(return_value=(b"", b""))
    list_proc = MagicMock()
    list_proc.returncode = 0
    list_proc.communicate = AsyncMock(return_value=(b"\n", b""))
    create_proc = MagicMock()
    create_proc.returncode = 0
    create_proc.communicate = AsyncMock(return_value=(b"https://x/pull/1\n", b""))

    side_effect = [rev_parse_proc, branch_proc, push_proc, list_proc, create_proc]

    async def fake_exec(*_args, **_kwargs):
        return side_effect.pop(0)

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await publish_job_commit(
            repo_path="/tmp/fake-repo",
            job_id=115,
            pr_title="fix: phone validator",
            pr_body="Closes bug 418af36c",
        )

    assert result.ok is True
    # Branch name follows CAI-RESP-078 GAP 1 convention.
    assert result.branch == "autofix/job-115-14ae9556"
    assert result.pr_url == "https://x/pull/1"


@pytest.mark.asyncio
async def test_publish_job_commit_propagates_push_failure():
    from legacy.ralph_runner import publish_job_commit

    rev_parse_proc = MagicMock()
    rev_parse_proc.returncode = 0
    rev_parse_proc.communicate = AsyncMock(return_value=(b"deadbeefcafebabe\n", b""))
    branch_proc = MagicMock()
    branch_proc.returncode = 0
    branch_proc.communicate = AsyncMock(return_value=(b"", b""))
    push_proc = MagicMock()
    push_proc.returncode = 128
    push_proc.communicate = AsyncMock(return_value=(b"", b"remote: Permission denied.\n"))

    side_effect = [rev_parse_proc, branch_proc, push_proc]

    async def fake_exec(*_args, **_kwargs):
        return side_effect.pop(0)

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await publish_job_commit(
            repo_path="/tmp/fake-repo",
            job_id=115,
            pr_title="x",
            pr_body="y",
        )

    assert result.ok is False
    assert result.stage == "push"
    assert "Permission denied" in result.error
