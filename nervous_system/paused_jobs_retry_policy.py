"""paused_jobs_retry_policy — PAUSED-JOBS-RETRY-POLICY-001 implementation.

Per CAI-RESP-105 authorization (cc-orchestrator owns AC scoping):
  (i)   Paused jobs older than X with result_summary matching the
        stale-error allowlist auto-retry once. Stale-error class:
        PostgREST schema-cache (PGRST204), REPOS.json mapping (alias
        gaps, fixed at runtime), transient network.
  (ii)  Paused jobs older than X NOT matching allowlist surface in
        boot_briefing for human/CAI review (separate migration adds the
        UNION branch — this module handles the auto-retry path).
  (iii) Permanent-pause patterns ('No commit produced — ghost success
        prevented') NEVER auto-retry. Existing paused_job_escalation
        Telegram path covers human notification at 1hr/6hr thresholds.
  (iv)  Self-audit derived from CAI-RESP-106 status='pending' bug:
        any reset asserts target status against the dispatcher's claim
        filter ('queued') so silent-no-op resets cannot ship.

Runs separately from paused_job_escalation.py (which handles Telegram
notification at 1hr/6hr). This module mechanically retries
allowlist-class casualties; the escalation module surfaces non-recovered
ones to humans. Both can run on the same cadence without coordination.

Auto-retry-once enforced via result_summary annotation. Re-pause after
auto-retry signals genuinely-stuck job → handed off to escalation.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.paused_jobs_retry_policy")

# Auto-retry threshold per AC scoping. cai's spec said "X days" but
# stale-error casualties (PostgREST cache refresh, REPOS.json alias fix)
# unstick within minutes — 1 hour is plenty for the cache to refresh and
# the alias to land. Configurable via constant; cai bumps if needed.
_AUTO_RETRY_AFTER_HOURS = 1

# Allowlist of stale-error patterns. Each is a regex matched against
# jobs.result_summary. Match → eligible for auto-retry.
_STALE_ERROR_ALLOWLIST = (
    # PostgREST schema-cache stale-read. PGRST204 typically refreshes
    # within minutes; jobs paused on this code 12+ days ago (job #92 in
    # session 2026-04-29 audit) just need a single retry to clear.
    re.compile(r"PGRST204|Could not find the .* column .* in the schema cache"),
    # REPOS.json mapping gap. Aliases get added (e.g. msg #815 added
    # 'hifz' → hifz-companion). Pre-fix paused jobs unstick on retry.
    re.compile(r"Repo '.*' not found in REPOS\.json"),
    # Transient network — connection timeouts, refused, reset by peer.
    # NOT including HTTP 5xx because those can be persistent service
    # outages, not transient.
    re.compile(r"connection (?:timed out|refused|reset)|TimeoutError|asyncio\.exceptions\.TimeoutError"),
)

# Permanent-pause patterns — NEVER auto-retry. The CC didn't commit;
# retrying spawns another CC that will likely no-op the same way.
# Per AC (iii): surfaced after Y > X via separate boot_briefing branch.
_PERMANENT_PAUSE_PATTERNS = (
    re.compile(r"No commit produced.*ghost success prevented"),
)

# Annotation marker so we don't auto-retry the same job twice. Future
# retries find this string in result_summary and skip.
_AUTO_RETRY_MARKER = "[paused_jobs_policy auto-retry"

# Dispatcher claims jobs with status='queued'. AC (iv): any reset MUST
# target this exact value or the reset is a silent no-op. The reset
# query below checks `RETURNING status` and asserts.
_DISPATCHER_CLAIM_STATUS = "queued"


def _classify(result_summary: str | None) -> str:
    """Classify a paused job's result_summary.

    Returns one of:
      - 'stale_error'  → allowlist match, eligible for auto-retry
      - 'permanent'    → ghost-success-prevented or similar; never retry
      - 'other'        → unknown shape; surface for human review
    """
    s = result_summary or ""
    for p in _PERMANENT_PAUSE_PATTERNS:
        if p.search(s):
            return "permanent"
    for p in _STALE_ERROR_ALLOWLIST:
        if p.search(s):
            return "stale_error"
    return "other"


def _already_auto_retried(result_summary: str | None) -> bool:
    """True if this job has already been auto-retried by a prior tick.

    Detection via _AUTO_RETRY_MARKER substring in result_summary. The
    annotation is appended on each auto-retry; we cap at one retry per
    job to avoid infinite loops on jobs that re-pause for the same
    reason (signals a genuine stuck state, not a stale-error casualty).
    """
    return _AUTO_RETRY_MARKER in (result_summary or "")


async def run_paused_jobs_retry_policy(
    supabase, bot=None, musa_chat_id: str | None = None
) -> dict:
    """Sweep paused jobs once. Returns counts dict for caller logging.

    Idempotent: jobs that have already been auto-retried (per result_summary
    annotation) are skipped on subsequent ticks.
    """
    counts = {"considered": 0, "retried": 0, "skipped_permanent": 0,
              "skipped_already_retried": 0, "skipped_other": 0, "errors": 0}
    try:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=_AUTO_RETRY_AFTER_HOURS)).isoformat()
        result = await supabase.table("jobs").select(
            "id, repo_name, status, fail_count, result_summary, updated_at"
        ).eq("status", "paused").gte("fail_count", 3).lt("updated_at", cutoff).execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"paused_jobs_retry_policy: query failed: {e}")
        error_tracker.track_exception("paused_jobs_retry_policy.query", e)
        counts["errors"] += 1
        return counts

    counts["considered"] = len(rows)
    for job in rows:
        try:
            classification = _classify(job.get("result_summary"))
            if classification == "permanent":
                counts["skipped_permanent"] += 1
                continue
            if classification == "other":
                counts["skipped_other"] += 1
                continue
            if _already_auto_retried(job.get("result_summary")):
                counts["skipped_already_retried"] += 1
                continue

            # Auto-retry: reset to status='queued', fail_count=0, append marker.
            now = datetime.now(timezone.utc).isoformat()
            annotation = (
                f" {_AUTO_RETRY_MARKER} {now} — stale-error class, "
                f"see PAUSED-JOBS-RETRY-POLICY-001]"
            )
            new_summary = (job.get("result_summary") or "") + annotation

            update = await supabase.table("jobs").update({
                "status": _DISPATCHER_CLAIM_STATUS,  # AC (iv): assert exact dispatcher filter
                "fail_count": 0,
                "result_summary": new_summary[:5000],  # cap at 5k chars
                "updated_at": now,
            }).eq("id", job["id"]).eq("status", "paused").execute()

            # AC (iv) self-audit: verify the write actually flipped status.
            # The .eq("status", "paused") guards against race; if it didn't
            # match, update.data is empty.
            if not update.data:
                logger.warning(
                    f"paused_jobs_retry_policy: job #{job['id']} reset no-op — "
                    f"raced or status changed mid-tick"
                )
                counts["errors"] += 1
                continue

            written_status = update.data[0].get("status")
            if written_status != _DISPATCHER_CLAIM_STATUS:
                logger.error(
                    f"paused_jobs_retry_policy: job #{job['id']} reset wrote "
                    f"status={written_status!r} — expected {_DISPATCHER_CLAIM_STATUS!r}. "
                    f"Substrate-bug self-catch per CAI-RESP-105 mitigation."
                )
                error_tracker.track_exception(
                    "paused_jobs_retry_policy.status_assertion",
                    AssertionError(f"job#{job['id']} got {written_status!r}"),
                )
                counts["errors"] += 1
                continue

            counts["retried"] += 1
            logger.info(
                f"paused_jobs_retry_policy: auto-retried job #{job['id']} "
                f"({job.get('repo_name')}) — class=stale_error"
            )
        except Exception as e:
            logger.error(
                f"paused_jobs_retry_policy: per-job exception for #{job.get('id')}: {e}"
            )
            error_tracker.track_exception("paused_jobs_retry_policy.per_job", e)
            counts["errors"] += 1
            continue

    if counts["retried"] or counts["skipped_other"] or counts["errors"]:
        logger.info(f"paused_jobs_retry_policy: tick complete — {counts}")
    return counts
