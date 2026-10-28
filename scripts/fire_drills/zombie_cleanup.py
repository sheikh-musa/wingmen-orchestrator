"""Zombie-job cleanup, extracted from legacy/wingmen_orch.py (Musa op#34845,
console's ruling): scripts/fire_drills/drill_sigkill.py exercises this real
logic to prove a SIGKILL mid-CLI leaves no zombies after an orchestrator
restart. It had no other live caller, so the original lived on in the
retired legacy/ cluster purely to serve this one drill -- moved here instead
of deleted so the drill keeps testing real code, not a reimplementation.

Console's review (bus #79116): this marks ALL 'running' jobs failed with no
age filter, which is correct ONLY at a real orchestrator's own startup (every
'running' row at that moment really is a zombie from the process that just
died). Call it ONLY with a mocked or ephemeral client in a fire drill or
test -- against a live client outside an actual orchestrator restart it
would fail every currently-running job.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("wingmen.fire_drill")


async def cleanup_zombie_jobs(supabase) -> int:
    """Mark all 'running' jobs as failed on startup — they were mid-execution when the process died.

    Spec conformance (TASK-033 + CC-UPDATE-015 deviation fix #3):
    - Marks status='failed' (not 'queued') — safer: prevents infinite restart
      loops on crash-causing jobs. Manual /resume required.
    - Matches ALL 'running' rows — startup semantics; no age filter needed.
    - Bumps fail_count so a repeatedly-zombied job eventually hits the
      3-fail pause threshold instead of flapping forever.
    """
    try:
        result = await (
            supabase.table("jobs")
            .select("id, repo_name, fail_count")
            .eq("status", "running")
            .execute()
        )
        zombies = result.data or []
        for job in zombies:
            new_fail_count = (job.get("fail_count") or 0) + 1
            await (
                supabase.table("jobs")
                .update({
                    "status": "failed",
                    "result_summary": "Zombie: was running when orchestrator restarted",
                    "fail_count": new_fail_count,
                })
                .eq("id", job["id"])
                .execute()
            )
            logger.warning(
                f"Zombie cleanup: job #{job['id']} ({job['repo_name']}) marked failed, "
                f"fail_count={new_fail_count}"
            )
        return len(zombies)
    except Exception as e:
        logger.error(f"Zombie job cleanup failed: {e}")
        return 0
