"""Semantic drift audit — LLM review of spec vs actual output on sampled jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random

logger = logging.getLogger("wingmen.semantic_drift")

SAFE_ENV_KEYS = {"PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "LC_ALL", "LC_CTYPE"}


async def _log_to_supabase(supabase, job_id, repo_name, message, level="info"):
    try:
        await supabase.table("build_log").insert({
            "job_id": job_id,
            "repo_name": repo_name,
            "phase": "semantic_drift",
            "message": message[:4000],
            "level": level,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to write build log: {e}")


async def run_drift_audit(supabase, sample_size=3, pool_size=20) -> None:
    """Sample N completed jobs from the last M, LLM-review each for spec drift."""
    try:
        resp = await supabase.table("work_outputs") \
            .select("job_id, repo_name, build_spec, cc_output_summary, diff_summary, files_changed, jobs!inner(status)") \
            .eq("jobs.status", "completed") \
            .order("created_at", desc=True) \
            .limit(pool_size) \
            .execute()

        if not resp.data:
            logger.debug("No completed jobs with work_outputs found")
            return

        already_audited_resp = await supabase.table("drift_audits") \
            .select("job_id") \
            .execute()
        audited_ids = {row["job_id"] for row in (already_audited_resp.data or [])}

        eligible = [row for row in resp.data if row["job_id"] not in audited_ids]
        if not eligible:
            logger.debug("No unaudited jobs eligible for drift audit")
            return

        sampled = random.sample(eligible, min(sample_size, len(eligible)))
        logger.info(f"Drift audit: sampling {len(sampled)} of {len(eligible)} eligible jobs")

        for row in sampled:
            await _audit_single_job(supabase, row)

    except Exception as e:
        logger.error(f"Drift audit error (non-blocking): {e}")


async def _audit_single_job(supabase, row: dict) -> None:
    job_id = row["job_id"]
    repo_name = row["repo_name"]
    build_spec = (row.get("build_spec") or "")[:3000]
    files_changed = row.get("files_changed") or []
    diff_summary = (row.get("diff_summary") or "")[:3000]
    cc_output = (row.get("cc_output_summary") or "")[:2000]

    if not build_spec and not diff_summary:
        logger.debug(f"Skipping job #{job_id}: no spec or diff to compare")
        return

    prompt = (
        "You are a semantic drift auditor. Compare the BUILD SPEC (what was requested) "
        "with the ACTUAL OUTPUT (what was done).\n\n"
        "Assess whether the agent's work semantically drifted from the spec's intent.\n\n"
        'Reply with EXACTLY this JSON format:\n'
        '{"verdict": "aligned|minor_drift|major_drift", '
        '"reasoning": "<1-2 sentences>", '
        '"drift_details": "<specific items that drifted, or null>"}\n\n'
        f"BUILD SPEC:\n{build_spec}\n\n"
        f"ACTUAL OUTPUT:\n"
        f"Files changed: {files_changed}\n"
        f"Diff summary: {diff_summary}\n"
        f"Agent summary: {cc_output}"
    )

    try:
        claude_bin = os.path.expanduser("~/.local/bin/claude")
        env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
        env["HOME"] = os.path.expanduser("~")
        env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", prompt, "--output-format", "text",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        raw = stdout.decode(errors="replace").strip()

        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            result = json.loads(raw[start:end])
            verdict = result.get("verdict", "minor_drift")
            if verdict not in ("aligned", "minor_drift", "major_drift"):
                verdict = "minor_drift"
            reasoning = result.get("reasoning", "No reasoning provided")
            drift_details = result.get("drift_details")
        except (ValueError, json.JSONDecodeError):
            logger.warning(f"Failed to parse LLM response for job #{job_id}: {raw[:200]}")
            verdict = "minor_drift"
            reasoning = "Failed to parse LLM response"
            drift_details = raw[:500] if raw else None

    except asyncio.TimeoutError:
        logger.warning(f"Claude CLI timed out for drift audit of job #{job_id}")
        verdict = "minor_drift"
        reasoning = "Claude CLI timed out"
        drift_details = None
    except Exception as e:
        logger.warning(f"Claude CLI error for drift audit of job #{job_id}: {e}")
        verdict = "minor_drift"
        reasoning = f"Claude CLI error: {e}"
        drift_details = None

    actual_output = f"Files: {files_changed}\nDiff: {diff_summary[:2000]}\nSummary: {cc_output[:1000]}"

    try:
        await supabase.table("drift_audits").insert({
            "job_id": job_id,
            "repo_name": repo_name,
            "build_spec": build_spec[:5000],
            "actual_output": actual_output[:5000],
            "verdict": verdict,
            "reasoning": reasoning[:2000],
            "drift_details": drift_details[:2000] if drift_details else None,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to insert drift_audits row for job #{job_id}: {e}")
        return

    level = {"aligned": "info", "minor_drift": "warn", "major_drift": "error"}.get(verdict, "warn")
    await _log_to_supabase(
        supabase, job_id, repo_name,
        f"Drift verdict: {verdict} — {reasoning}",
        level=level,
    )
    logger.info(f"  Drift audit job #{job_id}: {verdict} — {reasoning[:100]}")
