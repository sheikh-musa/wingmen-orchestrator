"""weekly_digest — Sunday 9PM strategic weekly review."""

from __future__ import annotations

import logging
import os
import sys
import time

# Ensure parent dir is on path for ai_provider import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_provider import call_ai

logger = logging.getLogger("wingmen.nervous_system.weekly_digest")

CTO_PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "CTO_PRINCIPLES.md")


async def _bug_summary_section(supabase) -> str:
    """Bug pipeline summary for the weekly digest."""
    try:
        from datetime import timedelta
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # All bugs from the last 7 days
        result = await supabase.table("bug_reports") \
            .select("id, status, reporter_source, repo_name, description, confidence, resolved_at, created_at") \
            .gte("created_at", week_ago) \
            .execute()

        bugs = result.data or []
        if not bugs:
            return ""

        total = len(bugs)
        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        by_repo: dict[str, int] = {}
        resolved = 0

        for b in bugs:
            s = b.get("status", "?")
            by_status[s] = by_status.get(s, 0) + 1
            src = b.get("reporter_source", "?")
            by_source[src] = by_source.get(src, 0) + 1
            repo = b.get("repo_name", "?")
            by_repo[repo] = by_repo.get(repo, 0) + 1
            if b.get("resolved_at"):
                resolved += 1

        # Also count still-open bugs (any status, not resolved)
        open_result = await supabase.table("bug_reports") \
            .select("id", count="exact") \
            .is_("resolved_at", "null") \
            .not_.in_("status", ["verified", "rejected"]) \
            .execute()
        open_count = open_result.count or 0

        lines = [
            f"\U0001f41e Bug Pipeline (last 7 days)",
            f"New: {total} \u00b7 Resolved: {resolved} \u00b7 Still open: {open_count}",
        ]

        if by_source:
            sources = ", ".join(f"{k}: {v}" for k, v in sorted(by_source.items(), key=lambda x: -x[1]))
            lines.append(f"Sources: {sources}")

        if by_repo and len(by_repo) > 1:
            repos = ", ".join(f"{k}: {v}" for k, v in sorted(by_repo.items(), key=lambda x: -x[1]))
            lines.append(f"Repos: {repos}")

        if open_count > 0:
            lines.append(f"\u26a0\ufe0f {open_count} bugs still need attention")
        else:
            lines.append("\u2705 All bugs resolved")

        return "\n".join(lines)

    except Exception:
        return ""


async def _feature_health_section(supabase) -> str:
    """Build the 4-line wingmen_features health section for the weekly
    digest. Returns an empty string if wingmen_features is missing or
    the data shape is unexpected (fail-soft)."""
    try:
        result = await supabase.table("wingmen_features").select(
            "slug, stage, health_signal, needs_backfill"
        ).execute()
    except Exception:
        return ""

    features = result.data or []
    if not features:
        return ""

    total = len(features)
    by_stage: dict[str, int] = {}
    for f in features:
        s = f.get("stage") or "unknown"
        by_stage[s] = by_stage.get(s, 0) + 1

    ga = by_stage.get("ga", 0)
    dogfood = by_stage.get("dogfood", 0)
    unknown = by_stage.get("unknown", 0)

    candidates = sum(
        1 for f in features
        if f.get("health_signal") == "ok"
        and f.get("stage") == "unknown"
        and not f.get("needs_backfill")
    )
    alerts = sum(1 for f in features if f.get("health_signal") == "alert")

    lines = [
        "\U0001f4cb Feature inventory: "
        f"{total} total \u00b7 {ga} ga \u00b7 {dogfood} dogfood \u00b7 {unknown} unknown",
    ]
    if candidates > 0:
        lines.append(
            f"\u26a0\ufe0f {candidates} health-green unknowns pending your review"
        )
    if alerts > 0:
        lines.append(f"\U0001f534 Alerts: {alerts}")
    else:
        lines.append("\U0001f7e2 Alerts: none")
    lines.append("\u2192 /tools in the ihsanos bot for details")

    return "\n".join(lines)


async def weekly_digest(supabase, bot, admin_chat_id: str):
    """Send Sunday 9PM weekly strategic review."""
    start = time.monotonic()
    logger.info("weekly_digest starting...")

    try:
        # Get last 7 days of snapshots
        from datetime import datetime, timezone, timedelta
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        result = await supabase.table("wingmen_brain").select("*").gte("snapshot_at", week_ago).order("snapshot_at", desc=True).execute()
        snapshots = result.data or []

        if not snapshots:
            logger.warning("No snapshots this week — skipping digest")
            return

        latest = snapshots[0]
        earliest = snapshots[-1] if len(snapshots) > 1 else snapshots[0]

        # Build week summary data
        repos_latest = {r["name"]: r for r in latest.get("repos", [])}
        repos_earliest = {r["name"]: r for r in earliest.get("repos", [])}

        summary_lines = []
        for name, r in repos_latest.items():
            commits = r.get("commits_7d", 0)
            health = r.get("health", "unknown")
            old_health = repos_earliest.get(name, {}).get("health", "unknown")
            change = ""
            if old_health != health:
                change = f" (was {old_health})"
            summary_lines.append(f"- {name}: {commits} commits, health={health}{change}, blockers={len(r.get('blockers', []))}")

        summary = "\n".join(summary_lines)

        # Load principles
        principles = ""
        if os.path.exists(CTO_PRINCIPLES_PATH):
            with open(CTO_PRINCIPLES_PATH) as f:
                principles = f.read()

        prompt = f"""You are Musa's CTO doing the weekly strategic review (Sunday evening).

{principles}

This week's project activity:
{summary}

Active clients: {len(latest.get('clients', []))}
Active jobs: {len(latest.get('job_queue', []))}
Total snapshots this week: {len(snapshots)}

Write a concise weekly digest covering:
1. What shipped this week (2-3 highlights)
2. Client health (who's active, who needs attention)
3. What should be the #1 focus next week and why
4. Barakah check: any open-source/community contributions this week?

Keep it under 300 words. Be direct."""

        try:
            digest = await call_ai(prompt, model="fast", max_tokens=1024)
        except Exception as e:
            logger.error(f"Weekly digest AI call failed: {e}")
            digest = "(Weekly analysis unavailable)"

        now = datetime.now(timezone.utc)
        message = f"\U0001f4ca Wingmen Weekly Digest \u2014 Week of {now.strftime('%d %b %Y')}\n\n{digest}"

        # Append UX analysis if we have enough event data (>= 10 events/week)
        try:
            from nervous_system.ux_analysis import generate_ux_report
            ux_section = await generate_ux_report(supabase)
            if ux_section:
                message += "\n\n" + ux_section
        except Exception as e:
            logger.warning(f"weekly_digest ux_analysis failed: {e}")

        # Append bug pipeline summary for the week
        try:
            bug_section = await _bug_summary_section(supabase)
            if bug_section:
                message += "\n\n" + bug_section
        except Exception as e:
            logger.warning(f"weekly_digest bug summary failed: {e}")

        # Append feature inventory health section (CTO Council session 2
        # option 4 — route the dogfood signal to where Musa already looks,
        # not to a page he has to remember to open). Fail-soft: if the
        # query fails, skip the section rather than break the digest.
        try:
            feature_section = await _feature_health_section(supabase)
            if feature_section:
                message += "\n\n" + feature_section
        except Exception as e:
            logger.warning(f"weekly_digest feature section failed: {e}")

        if len(message) <= 4000:
            await bot.send_message(chat_id=admin_chat_id, text=message)
        else:
            chunks = []
            current = ""
            for line in message.split("\n"):
                if len(current) + len(line) + 1 > 4000:
                    if current:
                        chunks.append(current.strip())
                    current = line + "\n"
                else:
                    current += line + "\n"
            if current.strip():
                chunks.append(current.strip())
            for chunk in chunks:
                await bot.send_message(chat_id=admin_chat_id, text=chunk)

        duration = int((time.monotonic() - start) * 1000)
        await supabase.table("brain_sync_log").insert({
            "task_name": "weekly_digest",
            "status": "success",
            "duration_ms": duration,
        }).execute()
        logger.info(f"weekly_digest sent in {duration}ms")

    except Exception as e:
        logger.error(f"weekly_digest failed: {e}")
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "weekly_digest", "status": "failed", "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
