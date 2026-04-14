"""Random audit — lightweight Claude CLI diff review on ~20% of successful builds."""

from __future__ import annotations

import asyncio
import logging
import os
import random

logger = logging.getLogger("wingmen.build_audit")

SAFE_ENV_KEYS = {"PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "LC_ALL", "LC_CTYPE"}


async def _log_to_supabase(supabase, job_id, repo_name, message, level="info"):
    try:
        await supabase.table("build_log").insert({
            "job_id": job_id,
            "repo_name": repo_name,
            "phase": "random_audit",
            "message": message[:4000],
            "level": level,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to write build log: {e}")


async def maybe_audit(repo_path: str, job_id: int, repo_name: str, supabase) -> None:
    """Run a random audit on ~20% of builds. Advisory only, never raises."""
    try:
        if random.random() > 0.2:
            return

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD~1", "--stat",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        stat_output = stdout.decode(errors="replace").strip()

        if not stat_output:
            return

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD~1",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        diff_text = stdout.decode(errors="replace")[:4000]

        review_prompt = (
            "Review this diff for obvious bugs, security issues, or broken patterns. "
            "Reply in under 100 words with issues found, or 'LGTM' if clean.\n\n"
            f"```\n{diff_text}\n```"
        )

        claude_bin = os.path.expanduser("~/.local/bin/claude")
        env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
        env["HOME"] = os.path.expanduser("~")
        env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", review_prompt, "--output-format", "text",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        review = stdout.decode(errors="replace").strip() or "(no review output)"

        await _log_to_supabase(supabase, job_id, repo_name, f"Audit result:\n{stat_output}\n\n{review}")
        logger.info(f"  Random audit for job #{job_id}: {review[:100]}")

    except Exception as e:
        logger.warning(f"  Random audit error (non-blocking): {e}")
        try:
            await _log_to_supabase(supabase, job_id, repo_name, f"Audit error: {e}", "warn")
        except Exception:
            pass
