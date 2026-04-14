"""QA Bridge — polls qa_findings and converts them to bug_reports.

Bridges structured QA output (CI, E2E tests, Lighthouse, manual QA, Sentry)
into the bug pipeline. Deduplicates against open bug reports before bridging.

Called every ~5 min from wingmen_orch.py main loop.
"""

from __future__ import annotations

import logging

from supabase import AsyncClient as SupabaseAsyncClient

logger = logging.getLogger("wingmen.qa_bridge")


async def poll_qa_findings(supabase: SupabaseAsyncClient) -> None:
    """Pick up new qa_findings, deduplicate, and bridge into bug_reports."""
    try:
        result = await supabase.table("qa_findings") \
            .select("*") \
            .eq("status", "new") \
            .order("created_at", desc=False) \
            .limit(10) \
            .execute()

        findings = result.data or []
        if not findings:
            return

        logger.info(f"qa_bridge: processing {len(findings)} new findings")

        for finding in findings:
            try:
                await _process_finding(supabase, finding)
            except Exception as e:
                logger.error(f"qa_bridge: failed to process finding {finding['id']}: {e}")

    except Exception as e:
        logger.error(f"qa_bridge poll failed: {e}")


async def _process_finding(supabase: SupabaseAsyncClient, finding: dict) -> None:
    """Process a single qa_finding: check for duplicates, then bridge."""
    finding_id = finding["id"]
    repo_name = finding["repo_name"]
    title = finding["title"]

    # Check for duplicate: same repo, similar title/description, open bug
    existing = await supabase.table("bug_reports") \
        .select("id, description") \
        .eq("repo_name", repo_name) \
        .not_.in_("status", ["verified", "rejected"]) \
        .execute()

    title_norm = title.lower().strip()
    desc_norm = finding.get("description", "").lower().strip()

    for bug in (existing.data or []):
        existing_desc = (bug.get("description") or "").lower().strip()
        if bug.get("id") and (
            f"qa_finding_id={finding_id}" in existing_desc
            or title_norm in existing_desc
            or (desc_norm[:80] and desc_norm[:80] == existing_desc[:80])
        ):
            await supabase.table("qa_findings").update({
                "status": "duplicate",
                "bug_report_id": bug["id"],
            }).eq("id", finding_id).execute()
            logger.info(f"qa_bridge: finding {finding_id} is duplicate of bug {bug['id']}")
            return

    # Bridge: create a new bug report
    from bug_pipeline import create_bug_report

    bug = await create_bug_report(
        supabase,
        client_id=None,
        reporter_name=f"QA/{finding['source']}",
        reporter_email=None,
        reporter_source="web",
        auth_provider="none",
        repo_name=repo_name,
        description=f"[{finding['source'].upper()}] {title}\n\n{finding['description']}",
        screenshot_url=finding.get("screenshot_url"),
        page_url=finding.get("page_url"),
        severity=finding.get("severity"),
    )

    # Update finding as bridged
    await supabase.table("qa_findings").update({
        "status": "bridged",
        "bug_report_id": bug["id"],
    }).eq("id", finding_id).execute()

    # Link bug report back to finding
    await supabase.table("bug_reports").update({
        "qa_finding_id": finding_id,
    }).eq("id", bug["id"]).execute()

    logger.info(f"qa_bridge: finding {finding_id} -> bug {bug['id']} (source={finding['source']})")
