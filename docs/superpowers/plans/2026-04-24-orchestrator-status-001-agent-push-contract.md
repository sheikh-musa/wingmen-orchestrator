# ORCHESTRATOR-STATUS-001 Option C Implementation Plan: Agent Push-Contract

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend cc-cosem's completion contract so that committing code implies pushing, opening a PR, and updating `bug_reports.status` to a truthful state — closing the "status says deployed but commit never left the worktree" gap observed on bug 418af36c.

**Architecture:** New `agents/git_publisher.py` module owns the push-and-PR step. `ralph_runner.py` invokes it after the existing `_merge_and_remove_worktree` step. `wingmen_orch.py` stops unconditionally marking `bug_reports.status='deployed'`; it sets `pr_open` on successful PR creation and hands off to cc-ihsanos's verification worker (Option B, separate plan) to flip to `deployed` once main-merge + production-deploy are observed. On push/PR failure, the `bug_notifier` already-present Telegram channel surfaces to Musa; the job advances to a terminal `push_failed` / `pr_failed` state rather than silent completion.

**Tech Stack:** Python 3.11+, asyncio, `asyncio.create_subprocess_exec` for git/gh, Supabase Python client (existing), pytest, pytest-asyncio.

**Coordination boundary:** This plan covers **agent-side publishing only**. The database state-machine expansion (`jobs.status` / `bug_reports.status` CHECK-or-comment change, plus the backfill for bug 418af36c) belongs to cc-ihsanos's Option B plan. Task 1 of this plan defines the new values this agent will *write*; the schema migration must land before Task 5 merges, otherwise inserts will reject under a future CHECK constraint.

**Answers to CAI's open questions:**
- **Q2 (auto-merge threshold):** No auto-merge by default. A `AUTO_MERGE_LOW_RISK` env flag is plumbed but defaults off; enabling it is a later, separately-reviewed change. Human ratifies every PR for now.
- **Q3 (failure escalation):** Reuse existing `bug_notifier.py` Telegram channel. On push or PR failure, notify Musa with the branch name + error summary; do not retry more than once (push once, PR once). The job is paused in its failure state, not retried silently.
- **Q4 (`deploy_url` column):** Confirmed at `schema.sql:100`. Already populated by `wingmen_orch.py:1320`. No schema change needed for this column.
- **Q1 is out of scope** (verification worker polling cadence — cc-ihsanos owns).

---

## File Structure

**New files:**
- `agents/git_publisher.py` — `publish_branch()` (push) and `open_pr()` (gh CLI) async functions, plus `PublisherResult` dataclass.
- `tests/test_git_publisher.py` — unit tests covering push/PR success, auth failure, network failure, duplicate-PR idempotency.

**Modified files:**
- `ralph_runner.py` — after `_merge_and_remove_worktree`, invoke `git_publisher.publish_and_open_pr(repo_path, branch, job, bug)` and propagate its `PublisherResult` back to the caller in `wingmen_orch.py`. (Modify `_merge_and_remove_worktree` to stop deleting the worktree-derived branch name so it's available downstream, or push the merge commit from `main` directly — see Task 4 design.)
- `wingmen_orch.py` — replace the unconditional `bug_reports.status='deployed'` update (lines 1315–1327) with a call to a new `_apply_publisher_result(supabase, job, bug, result)` helper. `jobs.status` transitions become: `running` → `pushed` (intermediate) → `pr_open` on success; `running` → `push_failed` / `pr_failed` on failure.
- `agents/fixer.py` — update the orphaned comment at line 35 (`Do NOT run git push — that's handled separately`) to point to `git_publisher.py`.
- `schema.sql` — documentation-only comment update on the `jobs` and `bug_reports` status comments, listing new values. (CHECK constraint introduction is deferred to cc-ihsanos's Option B migration to avoid two conflicting migrations landing.)

---

## Task 1: Define new status values + write the contract in a shared constants module

**Files:**
- Create: `agents/git_publisher_constants.py`
- Test: N/A (constants only; covered transitively by Task 2+)

- [ ] **Step 1: Create the constants file**

```python
# agents/git_publisher_constants.py
"""Status values written by the git publisher. Kept in one place so that
wingmen_orch, git_publisher, and the verification worker (cc-ihsanos,
separate plan) agree on the state-machine vocabulary.
"""

# jobs.status — extends the existing queued/running/completed/failed/paused
JOB_STATUS_PUSHED = "pushed"          # branch pushed, PR not yet opened
JOB_STATUS_PR_OPEN = "pr_open"        # PR opened, awaiting human merge
JOB_STATUS_PUSH_FAILED = "push_failed"
JOB_STATUS_PR_FAILED = "pr_failed"

# bug_reports.status — extends the existing new/diagnosing/proposed/
# approved/implementing/deployed
BUG_STATUS_PR_OPEN = "pr_open"
BUG_STATUS_PUSH_FAILED = "push_failed"
BUG_STATUS_PR_FAILED = "pr_failed"
# BUG_STATUS_DEPLOYED is set by cc-ihsanos's verification worker — not here.

PUBLISHER_STATUS_VALUES = {
    JOB_STATUS_PUSHED,
    JOB_STATUS_PR_OPEN,
    JOB_STATUS_PUSH_FAILED,
    JOB_STATUS_PR_FAILED,
}
```

- [ ] **Step 2: Commit**

```bash
git add agents/git_publisher_constants.py
git commit -m "feat(orch-status-001): add publisher status constants

Shared vocabulary for jobs.status and bug_reports.status values
introduced by the agent push-contract. Referenced by both
git_publisher.py (writer) and wingmen_orch.py (applier).

Part of ORCHESTRATOR-STATUS-001 Option C."
```

---

## Task 2: Write the failing test for `publish_branch` (happy path)

**Files:**
- Create: `tests/test_git_publisher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_git_publisher.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.git_publisher import publish_branch, PublisherResult


@pytest.mark.asyncio
async def test_publish_branch_pushes_with_correct_remote_and_refspec():
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)) as mock_exec:
        result = await publish_branch(
            repo_path="/tmp/fake-repo",
            branch="ralph-job-115",
        )

    assert result.ok is True
    assert result.error is None
    # Verify it invoked: git -C /tmp/fake-repo push origin ralph-job-115
    call_args = mock_exec.call_args
    assert call_args.args[0] == "git"
    assert "-C" in call_args.args
    assert "/tmp/fake-repo" in call_args.args
    assert "push" in call_args.args
    assert "origin" in call_args.args
    assert "ralph-job-115" in call_args.args
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_git_publisher.py::test_publish_branch_pushes_with_correct_remote_and_refspec -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'agents.git_publisher'`

---

## Task 3: Implement `publish_branch` happy path

**Files:**
- Create: `agents/git_publisher.py`

- [ ] **Step 1: Write the minimal implementation**

```python
# agents/git_publisher.py
"""Push agent-produced commits to origin and open PRs.

This module closes the ORCHESTRATOR-STATUS-001 gap: cc-cosem commits
locally in a worktree, but until this module was added, nothing pushed
those commits to origin — so bug_reports.status='deployed' was a lie.
"""
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


async def publish_branch(repo_path: str, branch: str) -> PublisherResult:
    """Push `branch` from `repo_path` to origin. Idempotent — re-running on
    an already-pushed branch is a no-op at the git layer.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "push", "origin", branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return PublisherResult(
            ok=False,
            branch=branch,
            error=(stderr or stdout).decode(errors="replace").strip(),
            stage="push",
        )
    return PublisherResult(ok=True, branch=branch)
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_git_publisher.py::test_publish_branch_pushes_with_correct_remote_and_refspec -v`
Expected: `PASS`

- [ ] **Step 3: Commit**

```bash
git add agents/git_publisher.py tests/test_git_publisher.py
git commit -m "feat(orch-status-001): git_publisher.publish_branch happy path

Pushes an agent-produced branch to origin. Returns PublisherResult
with ok=True on success, ok=False + stage='push' on failure.

Part of ORCHESTRATOR-STATUS-001 Option C."
```

---

## Task 4: Test and implement push-failure path

**Files:**
- Modify: `tests/test_git_publisher.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_publish_branch_returns_failure_on_auth_error():
    fake_proc = MagicMock()
    fake_proc.returncode = 128
    fake_proc.communicate = AsyncMock(return_value=(
        b"",
        b"remote: Permission to user/repo denied.\nfatal: unable to access\n",
    ))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        result = await publish_branch("/tmp/fake-repo", "ralph-job-115")

    assert result.ok is False
    assert result.stage == "push"
    assert "Permission" in result.error
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `pytest tests/test_git_publisher.py -v`
Expected: both tests PASS (Task 3 implementation already handles this path — this test codifies the contract).

- [ ] **Step 3: Commit**

```bash
git add tests/test_git_publisher.py
git commit -m "test(orch-status-001): push-failure path returns stage=push

Pins the PublisherResult shape so wingmen_orch can reliably inspect
result.stage to dispatch to the correct status transition."
```

---

## Task 5: Implement `open_pr` with gh CLI

**Files:**
- Modify: `agents/git_publisher.py`, `tests/test_git_publisher.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_open_pr_creates_pr_with_bug_metadata():
    from agents.git_publisher import open_pr

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(
        b"https://github.com/sheikh-musa/cosem-adcda/pull/6\n",
        b"",
    ))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)) as mock_exec:
        result = await open_pr(
            repo_path="/tmp/fake-repo",
            branch="ralph-job-115",
            title="fix(phone): accept read-only phone from auth",
            body="Closes bug 418af36c\n\nCommit: 14ae955",
        )

    assert result.ok is True
    assert result.pr_url == "https://github.com/sheikh-musa/cosem-adcda/pull/6"
    call_args = mock_exec.call_args
    assert call_args.args[0] == "gh"
    assert "pr" in call_args.args
    assert "create" in call_args.args
    assert "--head" in call_args.args
    assert "ralph-job-115" in call_args.args
    assert "--base" in call_args.args
    assert "main" in call_args.args
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_publisher.py::test_open_pr_creates_pr_with_bug_metadata -v`
Expected: `FAIL` with `ImportError: cannot import name 'open_pr'`

- [ ] **Step 3: Implement `open_pr`**

Append to `agents/git_publisher.py`:

```python
async def open_pr(
    repo_path: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> PublisherResult:
    """Open a PR from `branch` against `base` using gh CLI.

    Idempotent: if a PR already exists for this branch, gh prints the
    existing URL and exits 0 — still a success.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "create",
        "-R", _repo_slug_from_path(repo_path),
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
        # gh returns 1 with a useful message when PR already exists; treat as
        # idempotent success so retries don't spuriously fail.
        if "already exists" in err.lower():
            # Parse the existing URL from stderr: "a pull request for ... already exists: https://..."
            url = _extract_pr_url(err)
            return PublisherResult(ok=True, branch=branch, pr_url=url)
        return PublisherResult(ok=False, branch=branch, error=err, stage="pr")
    url = stdout.decode(errors="replace").strip().splitlines()[-1]
    return PublisherResult(ok=True, branch=branch, pr_url=url)


def _repo_slug_from_path(repo_path: str) -> str:
    """Read origin URL and derive owner/repo slug for gh -R."""
    import subprocess
    out = subprocess.check_output(
        ["git", "-C", repo_path, "config", "--get", "remote.origin.url"],
        text=True,
    ).strip()
    # git@github.com:owner/repo.git  OR  https://github.com/owner/repo.git
    out = out.removesuffix(".git")
    if out.startswith("git@"):
        return out.split(":", 1)[1]
    if "github.com/" in out:
        return out.split("github.com/", 1)[1]
    raise ValueError(f"cannot parse origin URL: {out}")


def _extract_pr_url(msg: str) -> Optional[str]:
    import re
    m = re.search(r"https?://\S*?/pull/\d+", msg)
    return m.group(0) if m else None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_git_publisher.py::test_open_pr_creates_pr_with_bug_metadata -v`
Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add agents/git_publisher.py tests/test_git_publisher.py
git commit -m "feat(orch-status-001): git_publisher.open_pr + repo-slug helper

Opens a PR via gh CLI. Idempotent on 'already exists' (treats as
success and returns the existing URL). Repo slug derived from
origin URL so the helper works for cosem-tdu, cosem-adcda, and
future sibling repos without config."
```

---

## Task 6: Test + implement `publish_and_open_pr` orchestrator

**Files:**
- Modify: `agents/git_publisher.py`, `tests/test_git_publisher.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_publish_and_open_pr_short_circuits_on_push_failure():
    from agents.git_publisher import publish_and_open_pr

    # Mock push to fail; open_pr must not be called.
    fake_push_proc = MagicMock()
    fake_push_proc.returncode = 1
    fake_push_proc.communicate = AsyncMock(return_value=(b"", b"push denied"))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_push_proc)) as mock_exec:
        result = await publish_and_open_pr(
            repo_path="/tmp/fake-repo",
            branch="ralph-job-115",
            title="x",
            body="y",
        )

    assert result.ok is False
    assert result.stage == "push"
    # Only one subprocess call — the push attempt. PR was not attempted.
    assert mock_exec.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_publisher.py::test_publish_and_open_pr_short_circuits_on_push_failure -v`
Expected: `FAIL` with `ImportError: cannot import name 'publish_and_open_pr'`

- [ ] **Step 3: Implement `publish_and_open_pr`**

Append to `agents/git_publisher.py`:

```python
async def publish_and_open_pr(
    repo_path: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> PublisherResult:
    """Push, then open PR. Short-circuits on push failure."""
    push_result = await publish_branch(repo_path, branch)
    if not push_result.ok:
        return push_result
    return await open_pr(repo_path, branch, title, body, base=base)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_git_publisher.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/git_publisher.py tests/test_git_publisher.py
git commit -m "feat(orch-status-001): publish_and_open_pr end-to-end

Chains push → PR. Short-circuits on push failure so open_pr never
runs against an unpushed branch (which would fail cryptically)."
```

---

## Task 7: Wire `publish_and_open_pr` into `ralph_runner._merge_and_remove_worktree`

**Files:**
- Modify: `ralph_runner.py` (function `_merge_and_remove_worktree`, currently around lines 59-85)

**Design decision:** The existing code deletes the `ralph-job-{id}` branch after merging it into the main working tree. For publishing, we either (a) push the merged commit on `main` and open no PR (fast-forward style), or (b) keep the branch and push it, opening a PR against `main`. Per CAI's "human ratifies every PR" default, we need a PR — so we pick (b).

- [ ] **Step 1: Read current implementation for context**

Run: `sed -n '59,90p' ralph_runner.py`

- [ ] **Step 2: Write the test (integration-flavored)**

```python
# tests/test_ralph_runner_publish.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_merge_and_publish_invokes_publisher_with_job_branch():
    from ralph_runner import _merge_and_publish

    mock_publish = AsyncMock(return_value=MagicMock(ok=True, pr_url="https://x/pull/1"))
    with patch("ralph_runner.publish_and_open_pr", mock_publish):
        result = await _merge_and_publish(
            repo_path="/tmp/repo",
            branch="ralph-job-115",
            pr_title="fix: phone",
            pr_body="bug 418af36c",
        )

    assert result.ok is True
    assert result.pr_url == "https://x/pull/1"
    mock_publish.assert_called_once_with(
        repo_path="/tmp/repo",
        branch="ralph-job-115",
        title="fix: phone",
        body="bug 418af36c",
    )
```

- [ ] **Step 3: Run test to confirm it fails**

Run: `pytest tests/test_ralph_runner_publish.py -v`
Expected: `FAIL` with `ImportError: cannot import name '_merge_and_publish'`

- [ ] **Step 4: Rename `_merge_and_remove_worktree` → `_merge_and_publish` and change behavior**

Modify `ralph_runner.py`. Keep the worktree-cleanup logic but skip the `git branch -D` so the branch survives for publishing. Push the branch from `repo_path` (which now contains the merged commit) rather than deleting it:

```python
from agents.git_publisher import publish_and_open_pr, PublisherResult


async def _merge_and_publish(
    repo_path: str,
    branch: str,
    pr_title: str,
    pr_body: str,
) -> PublisherResult:
    """Fast-forward merge the worktree branch into repo_path, delete the
    worktree dir (but NOT the branch — publisher needs it), then push and
    open a PR.
    """
    worktree_path = f"/tmp/wingmen-wt-{branch.split('-')[-1]}"

    # Fast-forward merge the worktree branch into repo_path's current HEAD.
    merge = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "merge", "--ff-only", branch,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, merge_err = await merge.communicate()
    if merge.returncode != 0:
        return PublisherResult(
            ok=False, branch=branch,
            error=f"ff merge failed: {merge_err.decode(errors='replace')}",
            stage="push",  # categorize as push-stage so downstream status lands on push_failed
        )

    # Remove the worktree dir. Leave the branch pointer intact for publishing.
    rm = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "worktree", "remove", "--force", worktree_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await rm.communicate()
    # Don't fail on worktree-remove errors — worktree may already be gone.

    return await publish_and_open_pr(
        repo_path=repo_path,
        branch=branch,
        title=pr_title,
        body=pr_body,
    )
```

- [ ] **Step 5: Update every call-site in `ralph_runner.py` to use the new name and pass PR metadata**

Run: `grep -n "_merge_and_remove_worktree" ralph_runner.py`

For each call site found, update to `_merge_and_publish(...)` and pass `pr_title` + `pr_body` derived from the job (see Task 8 for how these are constructed by `wingmen_orch`).

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_ralph_runner_publish.py -v`
Expected: `PASS`

- [ ] **Step 7: Commit**

```bash
git add ralph_runner.py tests/test_ralph_runner_publish.py
git commit -m "refactor(orch-status-001): ralph_runner now publishes, not just merges

Renames _merge_and_remove_worktree → _merge_and_publish. Keeps the
branch pointer alive (skips git branch -D), fast-forwards the merge,
removes the worktree dir, then calls publish_and_open_pr.

This closes the ORCHESTRATOR-STATUS-001 gap: every cc-cosem commit
now leaves origin/main in a state where the fix is actually
reviewable by a human."
```

---

## Task 8: Replace `bug_reports.status='deployed'` unconditional update in `wingmen_orch.py`

**Files:**
- Modify: `wingmen_orch.py` (around lines 1268-1327)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wingmen_orch_publisher_status.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.git_publisher import PublisherResult
from agents.git_publisher_constants import (
    JOB_STATUS_PR_OPEN, JOB_STATUS_PUSH_FAILED,
    BUG_STATUS_PR_OPEN, BUG_STATUS_PUSH_FAILED,
)


@pytest.mark.asyncio
async def test_apply_publisher_result_sets_pr_open_on_success():
    from wingmen_orch import _apply_publisher_result

    supabase = MagicMock()
    supabase.table = MagicMock(return_value=supabase)
    supabase.update = MagicMock(return_value=supabase)
    supabase.eq = MagicMock(return_value=supabase)
    supabase.execute = AsyncMock()

    await _apply_publisher_result(
        supabase,
        job={"id": 115, "triggered_by": "bug_report"},
        bug={"id": "418af36c"},
        result=PublisherResult(ok=True, branch="ralph-job-115", pr_url="https://x/pull/6"),
    )

    # Two updates: jobs.status='pr_open' + bug_reports.status='pr_open' with pr_url
    assert supabase.update.call_count == 2
    job_call = supabase.update.call_args_list[0]
    bug_call = supabase.update.call_args_list[1]
    assert job_call.args[0]["status"] == JOB_STATUS_PR_OPEN
    assert bug_call.args[0]["status"] == BUG_STATUS_PR_OPEN
    assert bug_call.args[0]["deploy_url"] is None  # NOT deployed yet


@pytest.mark.asyncio
async def test_apply_publisher_result_sets_push_failed_on_push_error():
    from wingmen_orch import _apply_publisher_result

    supabase = MagicMock()
    supabase.table = MagicMock(return_value=supabase)
    supabase.update = MagicMock(return_value=supabase)
    supabase.eq = MagicMock(return_value=supabase)
    supabase.execute = AsyncMock()

    await _apply_publisher_result(
        supabase,
        job={"id": 115, "triggered_by": "bug_report"},
        bug={"id": "418af36c"},
        result=PublisherResult(ok=False, branch="ralph-job-115", error="auth denied", stage="push"),
    )

    assert supabase.update.call_count == 2
    job_call = supabase.update.call_args_list[0]
    bug_call = supabase.update.call_args_list[1]
    assert job_call.args[0]["status"] == JOB_STATUS_PUSH_FAILED
    assert bug_call.args[0]["status"] == BUG_STATUS_PUSH_FAILED
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/test_wingmen_orch_publisher_status.py -v`
Expected: `FAIL` with `ImportError: cannot import name '_apply_publisher_result'`

- [ ] **Step 3: Implement `_apply_publisher_result` in `wingmen_orch.py`**

Add near the top of `wingmen_orch.py`:

```python
from agents.git_publisher import PublisherResult
from agents.git_publisher_constants import (
    JOB_STATUS_PR_OPEN, JOB_STATUS_PUSHED,
    JOB_STATUS_PUSH_FAILED, JOB_STATUS_PR_FAILED,
    BUG_STATUS_PR_OPEN, BUG_STATUS_PUSH_FAILED, BUG_STATUS_PR_FAILED,
)


async def _apply_publisher_result(supabase, job: dict, bug: dict | None, result: PublisherResult) -> None:
    """Translate a PublisherResult into jobs + bug_reports status updates.

    Called from the ralph_runner completion path in place of the old
    unconditional 'deployed' update. Never sets status='deployed' — that
    transition is owned by cc-ihsanos's verification worker (Option B).
    """
    if result.ok:
        job_status = JOB_STATUS_PR_OPEN
        bug_status = BUG_STATUS_PR_OPEN
    elif result.stage == "push":
        job_status = JOB_STATUS_PUSH_FAILED
        bug_status = BUG_STATUS_PUSH_FAILED
    else:  # stage == "pr"
        job_status = JOB_STATUS_PR_FAILED
        bug_status = BUG_STATUS_PR_FAILED

    await (
        supabase.table("jobs")
        .update({"status": job_status, "result_summary": result.pr_url or result.error})
        .eq("id", job["id"])
        .execute()
    )

    if job.get("triggered_by") == "bug_report" and bug:
        bug_update = {"status": bug_status}
        if result.pr_url:
            bug_update["pr_url"] = result.pr_url  # NOT deploy_url; not deployed
        await (
            supabase.table("bug_reports")
            .update(bug_update)
            .eq("id", bug["id"])
            .execute()
        )
```

- [ ] **Step 4: Remove the old unconditional `'deployed'` block at lines ~1315-1327; replace with a call to `_apply_publisher_result`**

Locate the block (current):

```python
if job.get("triggered_by") == "bug_report":
    _deploy_url = deploy_result.get("url") or ""
    _bug_update: dict = {"status": "deployed"}
    if _deploy_url:
        _bug_update["deploy_url"] = _deploy_url
    _bug_res = await (
        supabase.table("bug_reports")
        .update(_bug_update)
        .eq("job_id", job_id)
        .execute()
    )
```

Replace with:

```python
# publisher_result is returned up the call chain from _merge_and_publish
await _apply_publisher_result(supabase, job=job, bug=bug_row, result=publisher_result)
```

(`bug_row` is fetched a few lines earlier in the same function; if not, add a `SELECT` on `bug_reports WHERE job_id=job_id`.)

- [ ] **Step 5: Remove the `set_job_status(..., "completed", ...)` call** (current line 1268) — the new `_apply_publisher_result` now sets the correct terminal status.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_wingmen_orch_publisher_status.py -v`
Expected: both tests `PASS`.

- [ ] **Step 7: Commit**

```bash
git add wingmen_orch.py tests/test_wingmen_orch_publisher_status.py
git commit -m "feat(orch-status-001): wingmen_orch applies publisher results honestly

Replaces the unconditional bug_reports.status='deployed' update with
_apply_publisher_result, which maps a PublisherResult to
pr_open/push_failed/pr_failed. status='deployed' is no longer set
here; cc-ihsanos's verification worker (Option B) owns that
transition once main-merge + production-deploy are observed.

Closes the half of ORCHESTRATOR-STATUS-001 that lives in the
orchestrator's agent path. Schema state-machine expansion is a
separate migration owned by Option B."
```

---

## Task 9: Wire failure notifications through `bug_notifier`

**Files:**
- Modify: `agents/git_publisher.py` or new `agents/publisher_notifier.py`

- [ ] **Step 1: Check what `bug_notifier.py` currently exposes**

Run: `grep -n "^async def\|^def" bug_notifier.py | head -10`

- [ ] **Step 2: Write the failing test**

```python
@pytest.mark.asyncio
async def test_notify_on_publisher_failure_pings_telegram():
    from agents.git_publisher import notify_publisher_failure

    mock_notifier = AsyncMock()
    with patch("agents.git_publisher.send_telegram_alert", mock_notifier):
        await notify_publisher_failure(
            job_id=115,
            bug_id="418af36c",
            result=PublisherResult(ok=False, branch="ralph-job-115", error="auth denied", stage="push"),
        )

    mock_notifier.assert_called_once()
    msg = mock_notifier.call_args.args[0]
    assert "job #115" in msg
    assert "push" in msg.lower()
    assert "auth denied" in msg
```

- [ ] **Step 3: Run test to confirm it fails**

Expected: `FAIL` with `ImportError: cannot import name 'notify_publisher_failure'`.

- [ ] **Step 4: Implement `notify_publisher_failure`**

Append to `agents/git_publisher.py`:

```python
from bug_notifier import send_telegram_alert  # existing Musa-notifier


async def notify_publisher_failure(job_id: int, bug_id: Optional[str], result: PublisherResult) -> None:
    """Ping Musa on Telegram when push or PR creation fails. Fire-and-forget
    semantics — do not let a notifier failure mask the publisher failure.
    """
    stage = result.stage or "unknown"
    msg = (
        f"⚠️ cc-cosem publisher failed ({stage}) on job #{job_id}"
        + (f" (bug {bug_id})" if bug_id else "")
        + f"\nBranch: {result.branch}"
        + f"\nError: {result.error[:400] if result.error else '(no detail)'}"
    )
    try:
        await send_telegram_alert(msg)
    except Exception as e:  # noqa: BLE001
        # Log but do not re-raise; notifier failure must not mask publisher failure.
        import logging
        logging.getLogger(__name__).exception("publisher_failure notifier crashed", exc_info=e)
```

- [ ] **Step 5: Hook into `_apply_publisher_result`** (from Task 8) so it calls `notify_publisher_failure` when `not result.ok`.

Update `_apply_publisher_result` in `wingmen_orch.py`:

```python
from agents.git_publisher import notify_publisher_failure


async def _apply_publisher_result(supabase, job, bug, result):
    # ... existing status-update logic ...
    if not result.ok:
        await notify_publisher_failure(
            job_id=job["id"],
            bug_id=(bug or {}).get("id"),
            result=result,
        )
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/ -v -k "publisher"`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add agents/git_publisher.py wingmen_orch.py tests/
git commit -m "feat(orch-status-001): notify Musa on publisher failure

On push or PR failure, sends a Telegram alert via the existing
bug_notifier channel. Notifier failures are logged but swallowed
so they can't mask the publisher failure itself (per CAI Q3)."
```

---

## Task 10: Update `agents/fixer.py` comment + document the contract

**Files:**
- Modify: `agents/fixer.py` (line 35, the orphaned `Do NOT run git push — that's handled separately` comment)
- Modify: `schema.sql` (comment update listing new status values)

- [ ] **Step 1: Update fixer.py prompt comment**

In `agents/fixer.py`, locate `- Do NOT run git push — that's handled separately.` and change to:

```
- Do NOT run git push. The post-work step in ralph_runner.py
  invokes agents/git_publisher.py to push your branch to origin
  and open a PR. If you try to push yourself, the PR may already
  exist and your push may fail spuriously.
```

- [ ] **Step 2: Update `schema.sql` status comments**

In `schema.sql` near line 4-9 (jobs.status comment), expand to:

```sql
  -- queued | running | completed | failed | paused
  -- | pushed | pr_open | push_failed | pr_failed  (set by agents/git_publisher.py)
  -- | deployed  (set by verification worker, ORCHESTRATOR-STATUS-001 Option B — separate plan)
```

In `schema.sql` near line 100 area for `bug_reports.status`, make the same expansion. **Note:** this is a comment-only change. The CHECK constraint (if cc-ihsanos adds one in Option B) must be coordinated so agent-written values are accepted.

- [ ] **Step 3: Commit**

```bash
git add agents/fixer.py schema.sql
git commit -m "docs(orch-status-001): update fixer prompt + schema comments

fixer.py's orphaned 'handled separately' comment now points to
git_publisher.py. schema.sql status comments list the new agent-
written values. CHECK constraint introduction deferred to
Option B migration."
```

---

## Task 11: Backfill bug 418af36c (the incident that prompted this work)

**Files:**
- Create: `scripts/backfill_orchestrator_status_001.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Backfill the incident bug that exposed ORCHESTRATOR-STATUS-001.

Bug 418af36c-a0ae-4f55-b2a7-7fa0d993236b was marked status='deployed'
at 05:41 UTC on 2026-04-23, but the fix wasn't actually deployed until
c5fb68b was merged manually at ~08:33 UTC. This script corrects the
row with the actual deploy_url and a resolved_at that matches the
merge.
"""
import asyncio, os
from supabase import acreate_client

INCIDENT_BUG_ID = "418af36c-a0ae-4f55-b2a7-7fa0d993236b"
INCIDENT_DEPLOY_URL = "https://cosem-adcda.com"  # verify at run time
INCIDENT_RESOLVED_AT = "2026-04-23T08:33:00Z"    # merge time of c5fb68b


async def main():
    sb = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    res = await (
        sb.table("bug_reports")
        .update({
            "deploy_url": INCIDENT_DEPLOY_URL,
            "resolved_at": INCIDENT_RESOLVED_AT,
        })
        .eq("id", INCIDENT_BUG_ID)
        .execute()
    )
    print(f"updated {len(res.data)} row(s)")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the script in a staging environment first** (if one exists; otherwise against prod with a dry-run flag added)

Run: `python scripts/backfill_orchestrator_status_001.py`
Expected: `updated 1 row(s)`

- [ ] **Step 3: Verify in Supabase**

Run: `psql $DATABASE_URL -c "SELECT id, status, deploy_url, resolved_at FROM bug_reports WHERE id = '418af36c-a0ae-4f55-b2a7-7fa0d993236b';"`
Expected: `deploy_url` and `resolved_at` now populated; `status` already `deployed` from the original (incorrect) write — **leave as-is** to preserve the audit trail. Optionally add a `status_corrected_at` column in Option B's migration.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_orchestrator_status_001.py
git commit -m "chore(orch-status-001): backfill incident bug 418af36c

Populates deploy_url and resolved_at on the bug row that exposed
this whole class of issue. Preserves the original (incorrect)
status='deployed' write as-is for audit."
```

---

## Task 12: Integration smoke test against a real repo

**Files:**
- Create: `tests/integration/test_publisher_e2e.py`

- [ ] **Step 1: Write the smoke test**

```python
"""End-to-end smoke test: create a disposable branch, push it, open a PR,
close the PR. Guarded behind PUBLISHER_E2E=1 env so CI doesn't spam PRs.
"""
import os, pytest, subprocess, tempfile, uuid
from agents.git_publisher import publish_and_open_pr


@pytest.mark.skipif(os.environ.get("PUBLISHER_E2E") != "1",
                    reason="set PUBLISHER_E2E=1 to run integration smoke")
@pytest.mark.asyncio
async def test_publish_and_open_pr_against_sandbox_repo():
    # Use cosem-tdu as a live target — every user of this module already
    # has it cloned and authed. Create a trivial branch, assert PR opens,
    # close PR + delete branch.
    repo_path = os.path.expanduser("~/wingmen/projects/cosem-tdu")
    branch = f"publisher-e2e-{uuid.uuid4().hex[:8]}"

    subprocess.check_call(["git", "-C", repo_path, "checkout", "-b", branch, "origin/main"])
    subprocess.check_call(["git", "-C", repo_path, "commit", "--allow-empty", "-m", "publisher e2e test"])

    try:
        result = await publish_and_open_pr(
            repo_path=repo_path,
            branch=branch,
            title=f"publisher e2e {branch}",
            body="integration smoke — close me",
        )
        assert result.ok
        assert result.pr_url is not None
    finally:
        subprocess.call(["gh", "pr", "close", result.pr_url, "--delete-branch"], cwd=repo_path)
        subprocess.call(["git", "-C", repo_path, "checkout", "main"])
        subprocess.call(["git", "-C", repo_path, "branch", "-D", branch], stderr=subprocess.DEVNULL)
```

- [ ] **Step 2: Run the smoke test manually**

Run: `PUBLISHER_E2E=1 pytest tests/integration/test_publisher_e2e.py -v`
Expected: `PASS` and a temporary PR appears + is closed on cosem-tdu.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_publisher_e2e.py
git commit -m "test(orch-status-001): end-to-end publisher smoke against cosem-tdu

Gated behind PUBLISHER_E2E=1 so CI stays clean. Exercises the full
push → open PR → close PR lifecycle against a real repo. Run
manually before shipping any publisher change."
```

---

## Handoff + coordination checklist

Before merging any of these tasks into orchestrator `main`:

- [ ] **cc-ihsanos agreement** on the new `jobs.status` / `bug_reports.status` vocabulary. If Option B's migration lands first with a CHECK constraint that doesn't include `pr_open` etc., all Task 8 writes will reject.
- [ ] **`GITHUB_TOKEN` / gh auth** available in the orchestrator's runtime env. Verify `gh auth status` from wherever `wingmen_orch.py` runs. If not, plumb the same PAT that cc-cosem uses for pushes in Task 3 — secret name TBD (`ORCHESTRATOR_PUBLISHER_TOKEN`).
- [ ] **Run order:** Task 11 (backfill) should run *after* Option B's migration adds any missing columns (`verified_at` etc.); coordinate timing with cc-ihsanos.

---

## Self-review notes

**Spec coverage:** All four CAI open questions addressed (Q1 out of scope, Q2/Q3/Q4 answered in header). Incident backfill (Task 11) covered.

**Placeholder scan:** One intentional TBD — the `ORCHESTRATOR_PUBLISHER_TOKEN` secret name in the handoff checklist. That's a coordination point with the user, not a plan gap.

**Type consistency:** `PublisherResult` defined in Task 3; used in Tasks 4, 5, 6, 7, 8, 9. `publish_branch` / `open_pr` / `publish_and_open_pr` names consistent across Tasks 3, 5, 6, 7. Status constants defined in Task 1; imported in Tasks 8, 9.
