"""Unit tests for agents.git_publisher.

Subprocess calls to git and gh are mocked; no network, no real repos.
Integration smoke test lives separately and requires PUBLISHER_E2E=1.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------- publish_branch ----------

@pytest.mark.asyncio
async def test_publish_branch_pushes_with_correct_remote_and_refspec():
    from agents.git_publisher import publish_branch

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)) as mock_exec:
        result = await publish_branch(
            repo_path="/tmp/fake-repo",
            branch="autofix/job-115-14ae9556",
        )

    assert result.ok is True
    assert result.branch == "autofix/job-115-14ae9556"
    assert result.error is None
    args = mock_exec.call_args.args
    assert args[0] == "git"
    assert "-C" in args
    assert "/tmp/fake-repo" in args
    assert "push" in args
    assert "origin" in args
    assert "autofix/job-115-14ae9556" in args


@pytest.mark.asyncio
async def test_publish_branch_returns_failure_on_auth_error():
    from agents.git_publisher import publish_branch

    fake_proc = MagicMock()
    fake_proc.returncode = 128
    fake_proc.communicate = AsyncMock(return_value=(
        b"",
        b"remote: Permission to user/repo denied.\nfatal: unable to access\n",
    ))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        result = await publish_branch("/tmp/fake-repo", "autofix/job-115-abc")

    assert result.ok is False
    assert result.stage == "push"
    assert result.error is not None
    assert "Permission" in result.error


# ---------- build_branch_name ----------

def test_build_branch_name_uses_autofix_prefix_with_job_id_and_sha():
    from agents.git_publisher import build_branch_name

    assert build_branch_name(job_id=115, commit_sha="14ae9556e73dd63") == "autofix/job-115-14ae9556"
    assert build_branch_name(job_id=7, commit_sha="abcd") == "autofix/job-7-abcd"


def test_build_branch_name_rejects_empty_sha():
    from agents.git_publisher import build_branch_name

    with pytest.raises(ValueError):
        build_branch_name(job_id=1, commit_sha="")


# ---------- open_pr ----------

@pytest.mark.asyncio
async def test_open_pr_queries_existing_pr_first_then_creates_if_absent():
    """Per CAI-RESP-078 GAP 2: use `gh pr list --head` to detect existing PR
    instead of regex-parsing stderr from `gh pr create`.
    """
    from agents.git_publisher import open_pr

    # First call: `gh pr list` returns empty (no existing PR).
    list_proc = MagicMock()
    list_proc.returncode = 0
    list_proc.communicate = AsyncMock(return_value=(b"\n", b""))

    # Second call: `gh pr create` returns URL.
    create_proc = MagicMock()
    create_proc.returncode = 0
    create_proc.communicate = AsyncMock(return_value=(
        b"https://github.com/sheikh-musa/cosem-adcda/pull/6\n",
        b"",
    ))

    side_effect = [list_proc, create_proc]

    async def fake_exec(*_args, **_kwargs):
        return side_effect.pop(0)

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await open_pr(
            repo_path="/tmp/fake-repo",
            branch="autofix/job-115-abc",
            title="fix: phone",
            body="Closes bug 418af36c",
        )

    assert result.ok is True
    assert result.pr_url == "https://github.com/sheikh-musa/cosem-adcda/pull/6"


@pytest.mark.asyncio
async def test_open_pr_returns_existing_url_when_pr_already_exists():
    from agents.git_publisher import open_pr

    # First call: `gh pr list` returns an existing URL.
    list_proc = MagicMock()
    list_proc.returncode = 0
    list_proc.communicate = AsyncMock(return_value=(
        b"https://github.com/sheikh-musa/cosem-adcda/pull/5\n",
        b"",
    ))

    calls = {"n": 0}
    async def fake_exec(*_args, **_kwargs):
        calls["n"] += 1
        return list_proc  # would only be called once; create never fires

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await open_pr(
            repo_path="/tmp/fake-repo",
            branch="autofix/job-115-abc",
            title="x",
            body="y",
        )

    assert result.ok is True
    assert result.pr_url == "https://github.com/sheikh-musa/cosem-adcda/pull/5"
    assert calls["n"] == 1  # only the list call; create was skipped


@pytest.mark.asyncio
async def test_open_pr_returns_failure_when_create_fails():
    from agents.git_publisher import open_pr

    list_proc = MagicMock()
    list_proc.returncode = 0
    list_proc.communicate = AsyncMock(return_value=(b"\n", b""))
    create_proc = MagicMock()
    create_proc.returncode = 1
    create_proc.communicate = AsyncMock(return_value=(b"", b"GraphQL: Resource not accessible\n"))

    side_effect = [list_proc, create_proc]

    async def fake_exec(*_args, **_kwargs):
        return side_effect.pop(0)

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await open_pr(
            repo_path="/tmp/fake-repo",
            branch="autofix/job-115-abc",
            title="x",
            body="y",
        )

    assert result.ok is False
    assert result.stage == "pr"
    assert result.error is not None
    assert "Resource not accessible" in result.error


# ---------- publish_and_open_pr ----------

@pytest.mark.asyncio
async def test_publish_and_open_pr_short_circuits_on_push_failure():
    from agents.git_publisher import publish_and_open_pr

    fake_push_proc = MagicMock()
    fake_push_proc.returncode = 1
    fake_push_proc.communicate = AsyncMock(return_value=(b"", b"push denied"))

    calls = {"n": 0}
    async def fake_exec(*_args, **_kwargs):
        calls["n"] += 1
        return fake_push_proc

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await publish_and_open_pr(
            repo_path="/tmp/fake-repo",
            branch="autofix/job-115-abc",
            title="x",
            body="y",
        )

    assert result.ok is False
    assert result.stage == "push"
    # Only one subprocess call — the push attempt. PR list/create never happened.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_publish_and_open_pr_happy_path():
    from agents.git_publisher import publish_and_open_pr

    push_proc = MagicMock()
    push_proc.returncode = 0
    push_proc.communicate = AsyncMock(return_value=(b"", b""))
    list_proc = MagicMock()
    list_proc.returncode = 0
    list_proc.communicate = AsyncMock(return_value=(b"\n", b""))
    create_proc = MagicMock()
    create_proc.returncode = 0
    create_proc.communicate = AsyncMock(return_value=(b"https://x/pull/1\n", b""))

    side_effect = [push_proc, list_proc, create_proc]

    async def fake_exec(*_args, **_kwargs):
        return side_effect.pop(0)

    with patch("asyncio.create_subprocess_exec", fake_exec):
        result = await publish_and_open_pr(
            repo_path="/tmp/fake-repo",
            branch="autofix/job-115-abc",
            title="x",
            body="y",
        )

    assert result.ok is True
    assert result.pr_url == "https://x/pull/1"
    assert result.branch == "autofix/job-115-abc"
