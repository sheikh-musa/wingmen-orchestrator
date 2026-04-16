"""Shells out to Claude Code CLI to execute a build prompt."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from supabase import AsyncClient as SupabaseAsyncClient

# ── BUG-019 worktree isolation ────────────────────────────────────────────────

async def _create_worktree(repo_path: str, job_id: int) -> tuple[str, str]:
    """Create an isolated git worktree for job_id.

    Returns (worktree_path, branch_name). Cleans up any stale worktree
    left by a previous interrupted attempt with the same job_id.

    BUG-019: CC runs in the worktree so the main tree stays clean even
    if the orchestrator is killed mid-session. The main tree is untouched
    until _merge_and_remove_worktree merges the branch back.
    """
    branch = f"ralph-job-{job_id}"
    wt_path = f"/tmp/wingmen-wt-{job_id}"

    # Remove any stale worktree/branch from a previous failed attempt
    for cmd in (
        ["git", "worktree", "remove", "--force", wt_path],
        ["git", "branch", "-D", branch],
    ):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()  # ignore exit code — cleanup is best-effort

    # Create worktree on a fresh branch from current HEAD
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", wt_path, "-b", branch,
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed (job #{job_id}): {stderr.decode().strip()}"
        )

    return wt_path, branch


async def _merge_and_remove_worktree(
    repo_path: str, job_id: int, wt_path: str, branch: str
) -> None:
    """Merge worktree branch into main and clean up.

    Fast-forward only: safe because pick_next_jobs enforces one-job-per-repo.
    If CC made no commit the merge is a no-op ("Already up to date.").
    --force on worktree remove handles dirty/partially-written files.
    """
    async def _run_silent(cmd: list[str]) -> int:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            msg = stderr.decode().strip()
            if "Already up to date" not in msg:
                logger.warning(
                    f"BUG-019 worktree cleanup cmd {cmd[1]} failed for job #{job_id}: {msg}"
                )
        return proc.returncode

    await _run_silent(["git", "merge", "--ff-only", branch])
    await _run_silent(["git", "worktree", "remove", "--force", wt_path])
    await _run_silent(["git", "branch", "-D", branch])

logger = logging.getLogger("wingmen.ralph")

# Patterns to redact from logs
_SECRET_PATTERNS = re.compile(
    r'(sk-ant-[a-zA-Z0-9_-]+|eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+|'
    r'ghp_[a-zA-Z0-9]+|gho_[a-zA-Z0-9]+|vcp_[a-zA-Z0-9]+|sb_publishable_[a-zA-Z0-9_-]+)'
)


def _redact(text: str) -> str:
    return _SECRET_PATTERNS.sub('[REDACTED]', text)


async def _log_to_supabase(
    supabase: SupabaseAsyncClient,
    job_id: int,
    repo_name: str,
    phase: str,
    message: str,
    level: str = "info",
):
    try:
        await supabase.table("build_log").insert({
            "job_id": job_id,
            "repo_name": repo_name,
            "phase": phase,
            "message": _redact(message[:4000]),
            "level": level,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to write build log: {e}")


async def _check_commit_since(repo_path: str, since: datetime) -> bool:
    """Return True if at least one commit was made in repo_path since `since`.

    ARCH-021 Gate 1: prevents ghost successes where CC exited 0 but produced
    no commit (e.g., responded with a clarifying question instead of code).
    """
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    proc = await asyncio.create_subprocess_exec(
        "git", "log", "--oneline", f"--since={since_iso}", "-1",
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    return bool(stdout.strip())


async def _check_intent_alignment(
    repo_path: str,
    decision_text: str,
    result_summary: str,
) -> dict:
    """Call Haiku to verify the diff matches the stated task.

    ARCH-021 Gate 2: semantic drift check. Fails open if API is unavailable —
    never blocks a job on API outage.

    Returns {"aligned": bool, "confidence": int, "mismatches": list[str]}.
    """
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("ARCH-021 Gate 2: ANTHROPIC_API_KEY not set — skipping intent check")
            return {"aligned": True, "confidence": -1, "mismatches": [], "note": "api_key_missing"}

        # Get the diff for Gate 2 verification
        diff_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD~1", "HEAD",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        diff_bytes, _ = await asyncio.wait_for(diff_proc.communicate(), timeout=15)
        diff_text = diff_bytes.decode(errors="replace")[:8000]

        if not diff_text.strip():
            return {"aligned": True, "confidence": 10, "mismatches": [], "note": "no_diff_empty_commit"}

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "You are verifying whether a git diff implements a stated task.\n"
            "Return JSON only — no preamble:\n"
            "{\"aligned\": bool, \"confidence\": 0-10, \"mismatches\": [\"...\"]}\n\n"
            f"TASK:\n{decision_text[:3000]}\n\n"
            f"GIT DIFF:\n{diff_text}\n\n"
            f"CC RESULT SUMMARY:\n{result_summary[:500]}"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)
        return {
            "aligned": bool(result.get("aligned", True)),
            "confidence": int(result.get("confidence", 0)),
            "mismatches": result.get("mismatches", []),
        }
    except Exception as e:
        logger.warning(f"ARCH-021 Gate 2: intent check failed ({e}) — failing open")
        return {"aligned": True, "confidence": -1, "mismatches": [], "note": f"check_failed: {e}"}


async def run_claude(
    repo_path: str,
    prompt_text: str,
    job_id: int,
    repo_name: str,
    supabase: SupabaseAsyncClient,
    max_turns: int = 50,
    job_started_at: datetime | None = None,
    decision_text: str = "",
    commit_expected: bool = True,
) -> dict:
    """Run Claude Code CLI on a repo with a build prompt.

    Returns:
        {
            "success": bool,
            "summary": str,
            "gate1": dict | None,  # ARCH-021 commit existence result
            "gate2": dict | None,  # ARCH-021 intent alignment result
        }

    ARCH-021: Two verification gates run before marking success:
    Gate 1 — Commit existence: if no commit produced since job_started_at, ghost success.
    Gate 2 — Intent alignment: Haiku checks if diff matches the stated decision.
    """
    prompt_file = Path(tempfile.gettempdir()) / f"ralph_job_{job_id}.md"
    prompt_file.write_text(prompt_text)

    claude_bin = os.path.expanduser("~/.local/bin/claude")

    cmd = [
        claude_bin,
        "--dangerously-skip-permissions",
        "--max-turns", str(max_turns),
        "-p", prompt_text,
        "--output-format", "text",
    ]

    # Whitelist only safe env vars — never pass secrets to subprocess
    safe_keys = {"PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "LC_ALL", "LC_CTYPE"}
    env = {k: v for k, v in os.environ.items() if k in safe_keys}
    env["HOME"] = os.path.expanduser("~")
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    await _log_to_supabase(
        supabase, job_id, repo_name, "claude_start",
        f"Starting Claude CLI with max_turns={max_turns}", "info",
    )

    # Use job_started_at for Gate 1 precision; fall back to now if caller didn't supply it.
    launch_time = job_started_at or datetime.now(timezone.utc)

    # BUG-019: Create isolated worktree so CC never touches the main working tree.
    # Falls back to repo_path if git worktree add fails (non-git dir, permission, etc.)
    wt_path: str | None = None
    wt_branch: str | None = None
    try:
        wt_path, wt_branch = await _create_worktree(repo_path, job_id)
        active_path = wt_path
        await _log_to_supabase(
            supabase, job_id, repo_name, "worktree_created",
            f"BUG-019: worktree {wt_path} on branch {wt_branch}", "info",
        )
    except Exception as wt_err:
        logger.warning(
            f"BUG-019: worktree creation failed for job #{job_id} ({wt_err}) "
            f"— falling back to direct repo_path"
        )
        active_path = repo_path

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=active_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=1800  # 30 min max
        )

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        if stdout:
            await _log_to_supabase(
                supabase, job_id, repo_name, "claude_stdout",
                stdout[-4000:], "info",
            )
        if stderr:
            await _log_to_supabase(
                supabase, job_id, repo_name, "claude_stderr",
                stderr[-4000:], "warn",
            )

        success = process.returncode == 0 and len(stdout.strip()) > 0

        summary_lines = [
            line for line in stdout.strip().splitlines()[-20:]
            if line.strip()
        ]
        summary = "\n".join(summary_lines) or "(no output)"

        await _log_to_supabase(
            supabase, job_id, repo_name, "claude_done",
            f"exit_code={process.returncode} success={success}",
            "info" if success else "error",
        )

        gate1: dict | None = None
        gate2: dict | None = None

        # BUG-019: Merge worktree back to repo_path BEFORE Gate 1/Gate 2 so
        # that _check_commit_since and _check_intent_alignment see the commit.
        if wt_path and wt_branch:
            try:
                await _merge_and_remove_worktree(repo_path, job_id, wt_path, wt_branch)
                await _log_to_supabase(
                    supabase, job_id, repo_name, "worktree_merged",
                    f"BUG-019: worktree branch {wt_branch} merged into main", "info",
                )
            except Exception as merge_err:
                logger.warning(
                    f"BUG-019: worktree merge failed for job #{job_id}: {merge_err}"
                )
            finally:
                wt_path = None  # prevent double-cleanup in outer finally

        # ── ARCH-021 Gate 1: Commit existence ─────────────────────────────────
        if success and commit_expected:
            try:
                has_commit = await _check_commit_since(repo_path, launch_time)
                gate1 = {
                    "gate": "commit_check",
                    "passed": has_commit,
                    "launch_time": launch_time.isoformat(),
                }
                if not has_commit:
                    success = False
                    summary = (
                        "No commit produced — ghost success prevented "
                        "(clarifying question or no-op response)"
                    )
                    logger.warning(
                        f"ARCH-021 Gate 1 FAIL: job #{job_id} produced no commit"
                    )
                    await _log_to_supabase(
                        supabase, job_id, repo_name, "arch021_gate1_fail",
                        "Gate 1 FAIL: no commit since launch — ghost success prevented",
                        "error",
                    )
                else:
                    logger.debug(f"ARCH-021 Gate 1 OK: commit present for job #{job_id}")
            except Exception as e:
                logger.warning(f"ARCH-021 Gate 1 check error ({e}) — failing open")
                gate1 = {"gate": "commit_check", "passed": True, "note": f"check_error: {e}"}

        # ── ARCH-021 Gate 2: Intent alignment ─────────────────────────────────
        if success and decision_text:
            try:
                alignment = await _check_intent_alignment(repo_path, decision_text, summary)
                gate2 = {
                    "gate": "intent_alignment",
                    **alignment,
                    "passed": alignment["aligned"] and alignment.get("confidence", 0) >= 5,
                }
                if not gate2["passed"]:
                    # confidence < 0 means the API was unavailable — fail open
                    if gate2.get("confidence", 0) < 0:
                        logger.warning(
                            f"ARCH-021 Gate 2: API unavailable for job #{job_id} — skipping"
                        )
                        gate2["passed"] = True
                    else:
                        success = False
                        mismatches = gate2.get("mismatches", [])
                        summary = (
                            f"Semantic drift (confidence={gate2.get('confidence','?')}): "
                            f"{'; '.join(mismatches[:3])}"
                        )
                        logger.warning(
                            f"ARCH-021 Gate 2 FAIL: job #{job_id} semantic drift — {mismatches[:2]}"
                        )
                        await _log_to_supabase(
                            supabase, job_id, repo_name, "arch021_gate2_fail",
                            f"Gate 2 FAIL: confidence={gate2.get('confidence')} mismatches={mismatches}",
                            "error",
                        )
                else:
                    logger.debug(
                        f"ARCH-021 Gate 2 OK: aligned (confidence={gate2.get('confidence')}) "
                        f"for job #{job_id}"
                    )
            except Exception as e:
                logger.warning(f"ARCH-021 Gate 2 check error ({e}) — failing open")
                gate2 = {
                    "gate": "intent_alignment",
                    "passed": True,
                    "note": f"check_error: {e}",
                }

        return {
            "success": success,
            "summary": _redact(summary),
            "gate1": gate1,
            "gate2": gate2,
        }

    except asyncio.TimeoutError:
        if process:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
        await _log_to_supabase(
            supabase, job_id, repo_name, "claude_timeout",
            "Claude CLI timed out after 30 minutes", "error",
        )
        return {"success": False, "summary": "Claude CLI timed out after 30 minutes", "gate1": None, "gate2": None}

    except Exception as e:
        if process:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
        await _log_to_supabase(
            supabase, job_id, repo_name, "claude_error",
            str(e), "error",
        )
        return {"success": False, "summary": f"Error: {e}", "gate1": None, "gate2": None}

    finally:
        prompt_file.unlink(missing_ok=True)
        # BUG-019: Cleanup worktree on failure/exception paths.
        # wt_path is set to None after a successful merge in the try block,
        # so this only runs when the merge hasn't happened yet (timeout/error).
        if wt_path and wt_branch:
            try:
                await _merge_and_remove_worktree(repo_path, job_id, wt_path, wt_branch)
            except Exception as wt_e:
                logger.warning(
                    f"BUG-019: worktree cleanup (failure path) failed for job #{job_id}: {wt_e}"
                )
