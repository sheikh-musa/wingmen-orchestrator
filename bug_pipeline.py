"""Bug Pipeline — orchestrates the full bug report lifecycle.

Flow: create_report -> run_diagnosis -> route_approval -> apply_fix -> deploy -> verify
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from supabase import AsyncClient as SupabaseAsyncClient

from ai_provider import call_ai, extract_json
from agents.diagnostic import build_diagnostic_prompt, parse_diagnostic_response
import context_loader

logger = logging.getLogger("wingmen.bug_pipeline")

VALID_TRANSITIONS = {
    "new": ["diagnosing"],
    "diagnosing": ["proposed"],
    "proposed": ["approved", "rejected", "escalated"],
    "approved": ["deploying"],
    "deploying": ["deployed", "escalated"],
    "deployed": ["verified", "still_broken"],
    "still_broken": ["new", "escalated"],
    "rejected": [],
    "escalated": [],
    "verified": [],
}


async def create_bug_report(
    supabase: SupabaseAsyncClient,
    *,
    client_id: int | None,
    reporter_name: str,
    reporter_email: str | None,
    reporter_source: str,  # "telegram" | "web"
    auth_provider: str,  # "supabase" | "firebase" | "telegram" | "none"
    repo_name: str,
    description: str,
    screenshot_url: str | None = None,
    page_url: str | None = None,
) -> dict:
    """Create a new bug report and kick off diagnosis."""

    result = await supabase.table("bug_reports").insert({
        "client_id": client_id,
        "reporter_name": reporter_name,
        "reporter_email": reporter_email,
        "reporter_source": reporter_source,
        "auth_provider": auth_provider,
        "repo_name": repo_name,
        "description": description,
        "screenshot_url": screenshot_url,
        "page_url": page_url,
        "status": "new",
    }).execute()

    bug = result.data[0]
    logger.info(f"Bug report created: {bug['id']} for {repo_name}")

    # Kick off diagnosis in background
    asyncio.create_task(_run_diagnosis(supabase, bug))

    return bug


async def _update_status(
    supabase: SupabaseAsyncClient,
    bug_id: str,
    new_status: str,
    **extra_fields,
) -> dict:
    """Update bug report status with transition validation."""

    current = await supabase.table("bug_reports").select("status").eq("id", bug_id).single().execute()
    current_status = current.data["status"]

    if new_status not in VALID_TRANSITIONS.get(current_status, []):
        raise ValueError(f"Invalid transition: {current_status} -> {new_status}")

    update_data = {"status": new_status, **extra_fields}
    if new_status in ("verified", "rejected", "escalated"):
        update_data["resolved_at"] = datetime.now(timezone.utc).isoformat()

    result = await supabase.table("bug_reports").update(update_data).eq("id", bug_id).execute()
    return result.data[0]


async def _run_diagnosis(supabase: SupabaseAsyncClient, bug: dict) -> None:
    """Run AI diagnosis on a bug report."""
    bug_id = bug["id"]
    repo_name = bug["repo_name"]

    try:
        await _update_status(supabase, bug_id, "diagnosing")

        # Load repo context
        repo_config = context_loader.get_repo_config(repo_name)
        repo_path = repo_config.get("local_path", f"/Users/sheikhmusa/wingmen/projects/{repo_name}")

        # Get CLAUDE.md and recent commits
        claude_md = context_loader._read_file_safe(
            context_loader._resolve_path(repo_path) / "CLAUDE.md"
        )

        # Get recent git log
        import subprocess
        try:
            git_log = subprocess.check_output(
                ["git", "log", "--oneline", "-10"],
                cwd=repo_path, text=True, timeout=10
            )
        except Exception:
            git_log = "(could not read git log)"

        # If page_url provided, try to find the relevant source file
        relevant_code = ""
        if bug.get("page_url"):
            relevant_code = _find_relevant_code(repo_path, bug["page_url"], repo_config)

        repo_context = f"{claude_md}\n\n{relevant_code}".strip()

        # Build and run diagnostic prompt
        prompt = build_diagnostic_prompt(
            description=bug["description"],
            page_url=bug.get("page_url"),
            screenshot_description=None,  # TODO: extract from screenshot if present
            repo_path=repo_path,
            repo_context=repo_context[:8000],  # Cap context size
            recent_commits=git_log,
        )

        # Use vision model if screenshot present
        images = [bug["screenshot_url"]] if bug.get("screenshot_url") else None

        response = await call_ai(
            prompt,
            model="auto",
            images=images,
            max_tokens=4096,
            json_mode=True,
        )

        diagnosis = parse_diagnostic_response(response)

        # Update bug report with diagnosis
        await _update_status(
            supabase, bug_id, "proposed",
            confidence=diagnosis["confidence"],
            root_cause=diagnosis["root_cause"],
            affected_files=diagnosis["affected_files"],
            proposed_diff=diagnosis["proposed_diff"],
            diagnosis_full=diagnosis["diagnosis_full"],
        )

        logger.info(f"Bug {bug_id} diagnosed: confidence={diagnosis['confidence']}")

    except Exception as e:
        logger.error(f"Diagnosis failed for bug {bug_id}: {e}")
        # Mark as escalated if diagnosis fails
        try:
            await _update_status(supabase, bug_id, "escalated")
        except Exception:
            pass


def _find_relevant_code(repo_path: str, page_url: str, repo_config: dict) -> str:
    """Try to find source code relevant to a page URL."""

    # Next.js App Router: /dashboard/school/attendance -> src/app/dashboard/school/attendance/page.tsx
    if os.path.exists(os.path.join(repo_path, "src/app")):
        route = page_url.strip("/")
        candidates = [
            f"src/app/{route}/page.tsx",
            f"src/app/{route}/page.ts",
            f"src/app/{route}.tsx",
        ]
    # React/Vite: /attendance -> src/pages/Attendance.jsx
    elif os.path.exists(os.path.join(repo_path, "src/pages")):
        parts = page_url.strip("/").split("/")
        name = parts[-1].title() if parts else "Index"
        candidates = [
            f"src/pages/{name}.jsx",
            f"src/pages/{name}.tsx",
        ]
    else:
        return ""

    for candidate in candidates:
        full_path = os.path.join(repo_path, candidate)
        if os.path.exists(full_path):
            try:
                with open(full_path, "r") as f:
                    content = f.read()
                return f"--- {candidate} ---\n{content[:3000]}"
            except Exception:
                pass

    return ""


async def apply_fix(supabase: SupabaseAsyncClient, bug_id: str) -> None:
    """Apply the proposed fix via ralph_runner."""
    import ralph_runner

    bug = (await supabase.table("bug_reports").select("*").eq("id", bug_id).single().execute()).data

    await _update_status(supabase, bug_id, "deploying")

    repo_name = bug["repo_name"]
    repo_config = context_loader.get_repo_config(repo_name)
    repo_path = repo_config.get("local_path", f"/Users/sheikhmusa/wingmen/projects/{repo_name}")

    # Create a job for the fix
    job_result = await supabase.table("jobs").insert({
        "repo_name": repo_name,
        "description": f"Bug fix: {bug['root_cause'][:100]}",
        "status": "running",
        "priority": 1,
        "session_prompt": f"Apply this exact fix. Do not change anything else.\n\nDiff:\n{bug['proposed_diff']}\n\nCommit message: fix: {bug['root_cause'][:60]} (#BUG-{bug['id'][:8]})",
        "triggered_by": "bug_pipeline",
    }).execute()

    job_id = job_result.data[0]["id"]

    # Update bug with job reference
    await supabase.table("bug_reports").update({"job_id": job_id}).eq("id", bug_id).execute()

    # Run via ralph_runner
    try:
        result = await ralph_runner.run_claude(
            repo_path=repo_path,
            prompt_text=f"Apply this exact fix. Do not change anything else.\n\nDiff:\n{bug['proposed_diff']}\n\nCommit message: fix: {bug['root_cause'][:60]} (#BUG-{bug['id'][:8]})",
            job_id=job_id,
            repo_name=repo_name,
            supabase=supabase,
        )

        if result["success"]:
            await supabase.table("jobs").update({"status": "completed"}).eq("id", job_id).execute()
        else:
            raise RuntimeError(f"ralph_runner returned failure: {result['summary']}")

    except Exception as e:
        logger.error(f"Fix application failed for bug {bug_id}: {e}")
        await supabase.table("jobs").update({"status": "failed"}).eq("id", job_id).execute()
        await _update_status(supabase, bug_id, "escalated")
        raise


async def handle_verification(supabase: SupabaseAsyncClient, bug_id: str, verified: bool) -> None:
    """Handle reporter's verification response."""
    if verified:
        await _update_status(supabase, bug_id, "verified")
        logger.info(f"Bug {bug_id} verified as fixed")
    else:
        bug = (await supabase.table("bug_reports").select("retry_count").eq("id", bug_id).single().execute()).data
        retry_count = bug["retry_count"]

        if retry_count >= 2:
            await _update_status(supabase, bug_id, "escalated")
            logger.warning(f"Bug {bug_id} escalated after {retry_count} failed retries")
        else:
            await supabase.table("bug_reports").update({
                "status": "new",
                "retry_count": retry_count + 1,
                "confidence": None,
                "root_cause": None,
                "proposed_diff": None,
            }).eq("id", bug_id).execute()
            logger.info(f"Bug {bug_id} re-entered pipeline (retry {retry_count + 1})")
