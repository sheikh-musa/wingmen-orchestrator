"""Shells out to Claude Code CLI to execute a build prompt."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from supabase import AsyncClient as SupabaseAsyncClient

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

    process = None
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

        # Success if: clean exit with output
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

        return {"success": success, "summary": _redact(summary)}

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
        return {"success": False, "summary": "Claude CLI timed out after 30 minutes"}

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
        return {"success": False, "summary": f"Error: {e}"}

    finally:
        prompt_file.unlink(missing_ok=True)
