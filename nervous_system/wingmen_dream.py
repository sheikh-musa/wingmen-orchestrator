"""Wingmen Dream — scheduled memory consolidation subagent.

ARCH-020: Runs as a lightweight Haiku API call (NOT CC) to consolidate
the Wingmen memory layer: archive stale decisions, update repo summaries,
flag contradictions, and write a session_digest summarising the run.

Four-phase pattern (modelled on autoDream Orient/Gather/Consolidate/Prune):

  1. ORIENT  — query boot_briefing index, identify archival candidates
  2. GATHER  — pull high-value signals since last consolidation
  3. CONSOLIDATE — single Haiku API call, output structured JSON
  4. PRUNE   — archive terminal decisions + write session_digest

Trigger gates (all three must pass):
  1. Time:     last_consolidated_at < now() - 6 hours
  2. Activity: new rows in strategic_decisions or cc_work_sessions > 3
  3. Lock:     no consolidation currently in progress (in-process flag)

Called from wingmen_orch.py every 12 polls (~6 min). Gates ensure actual
Haiku API calls happen at most once every 6 hours and only when there is
meaningful new state to consolidate.

Cost ceiling: Haiku input ~4K tokens + output ~1K tokens < $0.002/run.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("wingmen.dream")

# ── Module-level state (in-process gate) ─────────────────────────────────────
_last_run: datetime | None = None
_running: bool = False

# Minimum hours between consolidation runs
MIN_HOURS_BETWEEN_RUNS = 6
# Minimum new DB rows to justify a consolidation run
MIN_ACTIVITY_THRESHOLD = 3

CONSOLIDATION_PROMPT = """You are the Wingmen memory consolidation agent. Your job is to analyse the current system state and output a structured JSON consolidation report. You have NO ability to write files, run code, or make external calls. You output ONLY valid JSON.

## Current system state

{state_summary}

## Your task

Analyse the state above and output a JSON object with exactly these keys:

{{
  "decisions_to_archive": ["list of decision_ref strings that are terminal and > 14 days old — these will be moved to archive"],
  "stale_repo_contexts": ["list of repo names where repo_context is > 7 days stale"],
  "contradictions_found": ["list of plain-English strings describing any contradictions between decisions, or between decisions and repo_context"],
  "stale_blockers": ["list of plain-English strings for any blocker that appears unactioned for > 7 days"],
  "digest_summary": "2-4 sentence plain English summary of the current system health for the session_digest topics field",
  "action_items": ["list of up to 5 concrete action items for Musa or the orchestrator"]
}}

Output ONLY the JSON object. No preamble, no explanation."""


# Per CAI-RESP-174 Q2: dream consolidation migrated from direct API
# (anthropic.Anthropic + Haiku) to call_ai(model='claude'), which resolves to
# cli_route via CAI-PROCESS-MAX-FIRST-001. Haiku-cheap rationale is void under
# Max plan. The _get_client() helper is retired with this migration.


async def _check_activity(supabase) -> int:
    """Return count of new rows since last run across strategic_decisions + cc_work_sessions."""
    global _last_run
    if _last_run is None:
        # First run — treat as having lots of activity to trigger
        return MIN_ACTIVITY_THRESHOLD + 1

    since = _last_run.isoformat()

    decisions_result = await (
        supabase.table("strategic_decisions")
        .select("id", count="exact")
        .gt("created_at", since)
        .execute()
    )
    sessions_result = await (
        supabase.table("cc_work_sessions")
        .select("id", count="exact")
        .gt("created_at", since)
        .execute()
    )

    decisions_new = decisions_result.count or 0
    sessions_new = sessions_result.count or 0
    total = decisions_new + sessions_new
    logger.debug(f"Dream activity check: +{decisions_new} decisions, +{sessions_new} sessions since last run = {total}")
    return total


def _build_state_summary(
    index_rows: list[dict],
    recent_decisions: list[dict],
    recent_sessions: list[dict],
    now: datetime,
) -> str:
    """Format orient + gather data into a compact prompt-friendly summary."""
    lines: list[str] = []

    cutoff_14d = (now - timedelta(days=14)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    # 1. Active decisions from index
    decisions = [r for r in index_rows if r.get("source") == "active_decision"]
    lines.append(f"## Active decisions ({len(decisions)} total)")
    for r in decisions:
        ctx = r.get("context", {})
        ref = r.get("key", "?")
        title = ctx.get("title", "?")
        status = ctx.get("challenge_status", "?")
        exec_s = ctx.get("execution_status") or ""
        decided_at = ctx.get("decided_at", "")
        age_note = ""
        if decided_at and decided_at < cutoff_14d:
            age_note = " [>14d — archival candidate]"
        terminal = status in ("implemented", "blocked", "overridden")
        if terminal:
            age_note += " [TERMINAL]"
        exec_note = f" exec={exec_s}" if exec_s else ""
        lines.append(f"  {ref} [{status}{exec_note}]: {title}{age_note}")

    # 2. Repo context from index
    repos = [r for r in index_rows if r.get("source") == "repo_context"]
    lines.append(f"\n## Repo contexts ({len(repos)} repos)")
    for r in repos:
        ctx = r.get("context", {})
        repo = r.get("key", "?")
        phase = ctx.get("phase", "?")
        test_h = ctx.get("test_health", "?")
        updated = ctx.get("updated_at", "")
        stale = " [STALE >7d]" if updated and updated < cutoff_7d else ""
        blockers = ctx.get("blockers") or []
        blocker_note = f" | blockers: {', '.join(blockers[:3])}" if blockers else ""
        lines.append(f"  {repo}: phase={phase} test={test_h}{stale}{blocker_note}")

    # 3. Recent new decisions (gather)
    if recent_decisions:
        lines.append(f"\n## New decisions since last consolidation ({len(recent_decisions)})")
        for d in recent_decisions[:10]:
            lines.append(f"  {d['decision_ref']} [{d.get('challenge_status','?')}]: {d['title'][:80]}")

    # 4. Recent sessions (gather)
    if recent_sessions:
        lines.append(f"\n## Recent CC sessions since last consolidation ({len(recent_sessions)})")
        for s in recent_sessions[:5]:
            outcome = s.get("outcome", "?")
            narrative = (s.get("narrative") or "")[:200]
            lines.append(f"  {s['repo_name']} [{outcome}]: {narrative}")

    # 5. Open QA failures
    failures = [r for r in index_rows if r.get("source") == "open_qa_failure"]
    if failures:
        lines.append(f"\n## Open QA failures ({len(failures)})")
        for r in failures:
            lines.append(f"  {r.get('key', '?')}")

    return "\n".join(lines)


async def run_dream(supabase) -> None:
    """Main entry point — called from wingmen_orch.py every 12 polls."""
    global _last_run, _running
    now = datetime.now(timezone.utc)

    # Gate 1: in-process lock
    if _running:
        logger.debug("Dream already running, skipping")
        return

    # Gate 2: time
    if _last_run is not None:
        elapsed_hours = (now - _last_run).total_seconds() / 3600
        if elapsed_hours < MIN_HOURS_BETWEEN_RUNS:
            logger.debug(f"Dream gate: only {elapsed_hours:.1f}h since last run, need {MIN_HOURS_BETWEEN_RUNS}h")
            return

    # Gate 3: activity
    activity = await _check_activity(supabase)
    if activity < MIN_ACTIVITY_THRESHOLD:
        logger.debug(f"Dream gate: only {activity} new rows, need {MIN_ACTIVITY_THRESHOLD}")
        return

    _running = True
    logger.info(f"Dream: starting consolidation run (activity={activity})")

    try:
        await _run_consolidation(supabase, now)
        _last_run = now
        logger.info("Dream: consolidation complete")
    except Exception as e:
        logger.error(f"Dream consolidation failed: {e}")
        raise
    finally:
        _running = False


async def _run_consolidation(supabase, now: datetime) -> None:
    """Execute the four consolidation phases."""

    # ── Phase 1: ORIENT ──────────────────────────────────────────────────────
    index_result = await supabase.table("boot_briefing").select("source,key,context").execute()
    index_rows = index_result.data or []
    logger.debug(f"Dream orient: {len(index_rows)} boot_briefing rows")

    # ── Phase 2: GATHER ──────────────────────────────────────────────────────
    since = (_last_run or (now - timedelta(hours=24))).isoformat()

    recent_decisions = (
        await supabase.table("strategic_decisions")
        .select("decision_ref,title,challenge_status,execution_status,decided_at")
        .gt("created_at", since)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    ).data or []
    recent_sessions = (
        await supabase.table("cc_work_sessions")
        .select("repo_name,outcome,narrative,created_at")
        .gt("created_at", since)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    ).data or []
    logger.debug(f"Dream gather: {len(recent_decisions)} new decisions, {len(recent_sessions)} new sessions")

    # ── Phase 3: CONSOLIDATE (call_ai → cli_route per CAI-RESP-174 Q2) ───────
    state_summary = _build_state_summary(index_rows, recent_decisions, recent_sessions, now)
    prompt = CONSOLIDATION_PROMPT.format(state_summary=state_summary)

    from ai_provider import call_ai, extract_json
    raw_output = await call_ai(
        prompt,
        model="claude",         # CAI-PROCESS-MAX-FIRST-001 resolves to cli_route
        max_tokens=1024,
        json_mode=True,
    )
    logger.debug(f"Dream consolidation response: {raw_output[:200]}...")

    # Parse JSON output
    parsed = extract_json(raw_output)
    if isinstance(parsed, dict):
        result: dict[str, Any] = parsed
    else:
        logger.error(f"Dream: consolidation output not a dict JSON: type={type(parsed).__name__}\nRaw: {raw_output[:500]}")
        result = {
            "decisions_to_archive": [],
            "stale_repo_contexts": [],
            "contradictions_found": [],
            "stale_blockers": [],
            "digest_summary": "Consolidation parse error — see logs.",
            "action_items": [],
        }

    # ── Phase 4: PRUNE + WRITE ───────────────────────────────────────────────
    archived_count = await _apply_archival(supabase, result.get("decisions_to_archive", []))
    await _write_session_digest(supabase, now, result, archived_count, state_summary, index_rows)

    # Log contradictions if found
    contradictions = result.get("contradictions_found", [])
    if contradictions:
        logger.warning(f"Dream contradictions found ({len(contradictions)}): {contradictions}")
    stale_blockers = result.get("stale_blockers", [])
    if stale_blockers:
        logger.warning(f"Dream stale blockers ({len(stale_blockers)}): {stale_blockers}")


async def _apply_archival(supabase, refs_to_archive: list[str]) -> int:
    """Move terminal decisions to archive if they pass the safety check."""
    if not refs_to_archive:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    archived = 0
    cutoff_14d = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    for ref in refs_to_archive:
        try:
            # Fetch the full row
            rows = (
                await supabase.table("strategic_decisions")
                .select("*")
                .eq("decision_ref", ref)
                .execute()
            ).data
            if not rows:
                logger.warning(f"Dream archive: {ref} not found in strategic_decisions")
                continue

            row = rows[0]
            # Safety check: only archive if truly terminal and old enough
            terminal = row.get("challenge_status") in ("implemented", "blocked", "overridden")
            old_enough = (row.get("created_at") or "") < cutoff_14d
            if not (terminal and old_enough):
                logger.info(f"Dream archive: skipping {ref} (terminal={terminal}, old_enough={old_enough})")
                continue

            # Build archive row — matching strategic_decisions_archive schema
            archive_row = {
                "id": row["id"],
                "decision_ref": row["decision_ref"],
                "title": row["title"],
                "decision": row.get("decision"),
                "reasoning": row.get("reasoning"),
                "repos_affected": row.get("repos_affected"),
                "source": row.get("source"),
                "challenge_status": row.get("challenge_status"),
                "bypass_review": row.get("bypass_review"),
                "notified_at": row.get("notified_at"),
                "execution_status": row.get("execution_status"),
                "completed_job_id": row.get("completed_job_id"),
                "completed_at": row.get("completed_at"),
                "category": row.get("category"),
                "parent_ref": row.get("parent_ref"),
                "created_at": row["created_at"],
                "archived_at": now,
            }
            await supabase.table("strategic_decisions_archive").upsert(
                archive_row, on_conflict="id"
            ).execute()
            await supabase.table("strategic_decisions").delete().eq("id", row["id"]).execute()
            archived += 1
            logger.info(f"Dream archive: moved {ref} to strategic_decisions_archive")
        except Exception as e:
            logger.error(f"Dream archive: failed to archive {ref}: {e}")

    return archived


async def _write_session_digest(
    supabase,
    now: datetime,
    result: dict[str, Any],
    archived_count: int,
    state_summary: str,
    index_rows: list[dict],
) -> None:
    """Write a session_digest row summarising this consolidation run."""
    digest_summary = result.get("digest_summary", "Memory consolidation run.")
    action_items = result.get("action_items", [])
    contradictions = result.get("contradictions_found", [])
    stale_blockers = result.get("stale_blockers", [])

    topics = ["memory_consolidation"]
    if archived_count > 0:
        topics.append(f"archived_{archived_count}_decisions")
    if contradictions:
        topics.append("contradictions_flagged")

    open_questions = contradictions + stale_blockers

    digest = {
        "session_date": now.date().isoformat(),
        "title": f"Wingmen Dream consolidation — {now.strftime('%Y-%m-%d %H:%M')} UTC",
        "topics_covered": topics,
        "key_reasoning": f"{digest_summary}\n\nState analysed:\n{state_summary[:800]}",
        "decisions_made": [],
        "open_questions": open_questions[:10],
        "action_items": action_items[:10],
        "chat_url": None,
        "council_sessions_referenced": [],
        "external_contacts": [],
        "repos_discussed": list({
            r.get("key", "") for r in index_rows
            if r.get("source") == "repo_context"
        }),
        "source": "wingmen_dream",
        "created_at": now.isoformat(),
    }

    await supabase.table("session_digests").insert(digest).execute()
    logger.info(f"Dream: wrote session_digest (archived={archived_count}, contradictions={len(contradictions)})")
