"""Post-build test gate — runs the repo's test suite before git push/deploy."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("wingmen.test_gate")

SAFE_ENV_KEYS = {"PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "LC_ALL", "LC_CTYPE"}
TEST_TIMEOUT = 300


async def _log_to_supabase(supabase, job_id, repo_name, message, level="info"):
    try:
        await supabase.table("build_log").insert({
            "job_id": job_id,
            "repo_name": repo_name,
            "phase": "test_gate",
            "message": message[:4000],
            "level": level,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to write build log: {e}")


async def run_tests(repo_path: str, repo_name: str, job_id: int, supabase) -> dict:
    """Auto-detect test infrastructure and run the repo's test suite."""
    repo = Path(repo_path)

    if (repo / "pytest.ini").exists() or (repo / "conftest.py").exists() or (repo / "tests").is_dir():
        test_cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
    elif (repo / "package.json").exists():
        try:
            pkg = json.loads((repo / "package.json").read_text())
            if "test" in pkg.get("scripts", {}):
                test_cmd = ["npm", "test", "--", "--ci"]
            else:
                await _log_to_supabase(supabase, job_id, repo_name, "No test script in package.json, skipping")
                return {"passed": True, "output": "No test script in package.json, skipping", "skipped": True}
        except (json.JSONDecodeError, OSError):
            await _log_to_supabase(supabase, job_id, repo_name, "Could not parse package.json, skipping tests")
            return {"passed": True, "output": "Could not parse package.json, skipping", "skipped": True}
    else:
        await _log_to_supabase(supabase, job_id, repo_name, "No test infrastructure detected, skipping")
        return {"passed": True, "output": "No test infrastructure detected, skipping", "skipped": True}

    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    env["HOME"] = os.path.expanduser("~")
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    try:
        proc = await asyncio.create_subprocess_exec(
            *test_cmd,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT)
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        passed = proc.returncode == 0

        level = "info" if passed else "error"
        await _log_to_supabase(supabase, job_id, repo_name, f"Tests {'passed' if passed else 'failed'}: {output[-2000:]}", level)

        return {"passed": passed, "output": output, "skipped": False}

    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        msg = f"Tests timed out after {TEST_TIMEOUT}s"
        await _log_to_supabase(supabase, job_id, repo_name, msg, "error")
        return {"passed": False, "output": msg, "skipped": False}

    except Exception as e:
        msg = f"Test runner error: {e}"
        await _log_to_supabase(supabase, job_id, repo_name, msg, "error")
        return {"passed": False, "output": msg, "skipped": False}
