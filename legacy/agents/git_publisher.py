"""Push agent-produced commits to origin and open PRs.

Closes the ORCHESTRATOR-STATUS-001 gap: cc-cosem commits locally in
a worktree, but until this module was added, nothing pushed those
commits to origin — so bug_reports.status='deployed' was a lie.

Design per CAI-RESP-078 foldings:
- Branch naming: autofix/job-<job_id>-<short_sha> (GAP 1 ruling).
- Idempotency: query with `gh pr list --head` before `gh pr create`
  instead of regex-parsing stderr (GAP 2 ruling).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class PublisherResult:
    ok: bool
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    error: Optional[str] = None
    stage: Optional[str] = None  # "push" | "pr" | None on success


def build_branch_name(job_id: int, commit_sha: str) -> str:
    """Return the canonical branch name for a publisher-managed branch.

    Format: autofix/job-<job_id>-<first 8 chars of sha>. The short_sha
    disambiguates re-runs of the same job_id (which shouldn't happen
    but defense-in-depth per CAI-RESP-078 GAP 1).
    """
    if not commit_sha:
        raise ValueError("commit_sha must be non-empty")
    return f"autofix/job-{job_id}-{commit_sha[:8]}"


async def publish_branch(repo_path: str, branch: str) -> PublisherResult:
    """Push `branch` from `repo_path` to origin.

    Idempotent at the git layer — re-running on an already-pushed
    branch is a no-op.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "push", "origin", branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or stdout).decode(errors="replace").strip()
        return PublisherResult(ok=False, branch=branch, error=err, stage="push")
    return PublisherResult(ok=True, branch=branch)


async def _existing_pr_url(repo_path: str, branch: str) -> Optional[str]:
    """Return the URL of an open PR with head=<branch>, or None."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "list",
        "--head", branch,
        "--json", "url",
        "--jq", ".[0].url // \"\"",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_path,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    url = stdout.decode(errors="replace").strip()
    return url if url else None


async def open_pr(
    repo_path: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> PublisherResult:
    """Open a PR from `branch` against `base`. Idempotent: returns the
    URL of an existing open PR if one already has head=<branch>.
    """
    existing = await _existing_pr_url(repo_path, branch)
    if existing:
        return PublisherResult(ok=True, branch=branch, pr_url=existing)

    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "create",
        "--head", branch,
        "--base", base,
        "--title", title,
        "--body", body,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_path,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or stdout).decode(errors="replace").strip()
        return PublisherResult(ok=False, branch=branch, error=err, stage="pr")

    # gh pr create prints the URL as the last line of stdout.
    lines = stdout.decode(errors="replace").strip().splitlines()
    url = lines[-1] if lines else None
    return PublisherResult(ok=True, branch=branch, pr_url=url)


async def publish_and_open_pr(
    repo_path: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> PublisherResult:
    """Push, then open PR. Short-circuits on push failure so open_pr
    never runs against an unpushed branch (which would fail cryptically).
    """
    push_result = await publish_branch(repo_path, branch)
    if not push_result.ok:
        return push_result
    return await open_pr(repo_path, branch, title, body, base=base)
