"""
bug_reports_poll.py — Watch bug_reports for status='new', auto-spec and create jobs.

Polls the orchestrator Supabase for new user-reported bugs and converts each
one into a pending_review job. CC's adversarial review gate then validates the
auto-generated spec before ralph executes.

Both bug_reports and jobs live in the orchestrator Supabase project, so a single
client handles both reads and writes.

Flow:
  bug_reports (status='new', job_id IS NULL)
    → generate structured spec from description + page_url
    → jobs INSERT (status='pending_review', triggered_by='bug_report')
    → bug_reports UPDATE (status='diagnosing', job_id=<new_id>)

CC review catches domain/implementation gaps before ralph touches anything.
If CC challenges a bug spec, Musa sees it in Telegram and can clarify.

Rate: every main loop poll (~30s). Processes up to 3 bugs per cycle to
avoid overwhelming the review queue.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("wingmen.bug_reports_poll")


def _build_spec(bug: dict) -> str:
    """Generate a structured job spec from a bug report row."""
    description = (bug.get("description") or "").strip()
    page_url = bug.get("page_url") or "(not provided)"
    reporter_name = bug.get("reporter_name") or "Anonymous"
    repo_name = bug.get("repo_name") or "ihsanos"
    bug_id = bug.get("id", "?")

    return f"""# Bug Fix: {description[:100]}

## Bug Report
ID: {bug_id}
Reporter: {reporter_name}
Page: {page_url}
Repo: {repo_name}

## Description
{description}

## Task
Investigate and fix the reported bug using only Read/Glob/Grep to explore the
codebase. Do not guess at the root cause — read the relevant files first.

1. Identify root cause by reading the code at or near the page: {page_url}
2. Implement the minimal fix — do not refactor unrelated code
3. Verify the fix addresses the exact behaviour described above

## Acceptance Criteria
- The specific behaviour described above no longer occurs
- No regressions in related functionality
- If DB touched: RLS policies intact, NUMERIC(15,2) for money, org_id on new rows
- Commit produced with a clear message referencing the bug description
- Do NOT void or delete existing data to work around the bug
"""


async def poll_bug_reports(supabase) -> None:
    """Pick up new bug reports and create pending_review jobs.

    Called every main loop iteration (~30s). Processes up to 3 per cycle.
    Both bug_reports and jobs are in the orchestrator Supabase project.
    """
    try:
        result = await (
            supabase.table("bug_reports")
            .select(
                "id, repo_name, description, page_url, "
                "reporter_name, reporter_email, created_at"
            )
            .eq("status", "new")
            .is_("job_id", "null")
            .order("created_at", desc=False)
            .limit(3)
            .execute()
        )

        bugs = result.data or []
        if not bugs:
            return

        logger.info(f"bug_reports_poll: {len(bugs)} new bug(s) to process")

        for bug in bugs:
            bug_id = bug["id"]
            repo_name = (bug.get("repo_name") or "ihsanos").strip()
            description = (bug.get("description") or "")[:80]

            spec = _build_spec(bug)

            try:
                job_result = await (
                    supabase.table("jobs")
                    .insert({
                        "repo_name": repo_name,
                        "description": f"[BUG] {description}",
                        "session_prompt": spec,
                        "status": "pending_review",
                        "priority": 3,          # higher than default (5)
                        "triggered_by": "bug_report",
                    })
                    .execute()
                )

                if not job_result.data:
                    logger.error(
                        f"bug_reports_poll: job insert returned no data for bug {bug_id}"
                    )
                    continue

                job_id: int = job_result.data[0]["id"]

                await (
                    supabase.table("bug_reports")
                    .update({"status": "diagnosing", "job_id": job_id})
                    .eq("id", bug_id)
                    .execute()
                )

                logger.info(
                    f"bug_reports_poll: bug {bug_id} → job #{job_id} "
                    f"({repo_name} — {description[:50]})"
                )

            except Exception as e:
                logger.error(f"bug_reports_poll: failed processing bug {bug_id}: {e}")
                try:
                    from nervous_system import error_tracker
                    error_tracker.track_exception("bug_reports_poll.process_bug", e)
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"bug_reports_poll failed: {e}")
        try:
            from nervous_system import error_tracker
            error_tracker.track_exception("bug_reports_poll.main", e)
        except Exception:
            pass
