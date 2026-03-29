"""Shells out to Claude Code CLI to execute a build prompt."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from supabase import AsyncClient as SupabaseAsyncClient

logger = logging.getLogger("wingmen.ralph")


async def _log_to_supabase(
    supabase: SupabaseAsyncClient,
    job_id: int,
    repo_name: str,
    phase: str,
    message: str,
    level: str = "info",
):
    await supabase.table("build_log").insert({
        "job_id": job_id,
        "repo_name": repo_name,
        "phase": phase,
        "message": message[:4000],  # truncate long messages
        "level": level,
    }).execute()


async def run_claude(
    repo_path: str,
    prompt_text: str,
    job_id: int,
    repo_name: str,
    supabase: SupabaseAsyncClient,
    max_turns: int = 50,
) -> dict:
    """Run Claude Code CLI on a repo with a build prompt.

    Returns {"success": bool, "summary": str}.
    """
    # Write prompt to temp file
    prompt_file = Path(tempfile.gettempdir()) / f"ralph_job_{job_id}.md"
    prompt_file.write_text(prompt_text)

    completion_promise = f"JOB_{job_id}_DONE"

    claude_bin = os.path.expanduser("~/.local/bin/claude")

    cmd = [
        claude_bin,
        "--dangerously-skip-permissions",
        "--max-turns", str(max_turns),
        "-p", prompt_text,
        "--output-format", "text",
    ]

    # Don't pass ANTHROPIC_API_KEY — claude CLI uses Max subscription auth
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    await _log_to_supabase(
        supabase, job_id, repo_name, "claude_start",
        f"Starting Claude CLI with max_turns={max_turns}", "info",
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=1800  # 30 min max
        )

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        # Log output
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

        # Success if: promise found, OR clean exit (exit_code=0) with output
        success = process.returncode == 0 and (
            completion_promise in stdout or len(stdout.strip()) > 0
        )

        # Extract a summary (last meaningful lines)
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

        return {"success": success, "summary": summary}

    except asyncio.TimeoutError:
        await _log_to_supabase(
            supabase, job_id, repo_name, "claude_timeout",
            "Claude CLI timed out after 30 minutes", "error",
        )
        return {"success": False, "summary": "Claude CLI timed out after 30 minutes"}

    except Exception as e:
        await _log_to_supabase(
            supabase, job_id, repo_name, "claude_error",
            str(e), "error",
        )
        return {"success": False, "summary": f"Error: {e}"}

    finally:
        prompt_file.unlink(missing_ok=True)
