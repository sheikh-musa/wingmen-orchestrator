"""UX Analysis — weekly AI-powered analysis of ui_events data.

Runs as part of the weekly digest (piggybacked on the Sunday 9PM
nervous system task). Queries ui_events for the last 7 days, computes
flow completion rates, rage click hotspots, and idle abandon points,
then asks Claude to generate 3 specific UX recommendations ranked by
impact.

The output is appended to the weekly digest Telegram message. Each
recommendation can be actioned by opening a CTO Council session via
/rule in the bot.

Cycle: ui_events → analysis → recommendation → fix → measure.
Origin: claude.ai UX strategy (2026-04-12). "Instrument first,
iterate with confidence."
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("wingmen.ux_analysis")


def _env(name: str) -> str:
    raw = os.environ.get(name, "")
    return raw.split("#", 1)[0].strip()


async def generate_ux_report(supabase) -> str:
    """Query ui_events for the past 7 days and generate an AI analysis.

    Returns a formatted text block suitable for appending to the weekly
    digest Telegram message. Returns empty string if insufficient data
    (< 10 events) — no point analyzing noise.
    """
    try:
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # Fetch all events from the past week
        result = await supabase.table("ui_events") \
            .select("event_name, properties, created_at") \
            .eq("repo", "ihsanos") \
            .gte("created_at", week_ago) \
            .order("created_at", desc=False) \
            .execute()

        events = result.data or []
        if len(events) < 10:
            return ""  # Not enough data to analyze

        # Compute summary stats
        stats = _compute_stats(events)
        if not stats:
            return ""

        # Generate AI analysis
        from ai_provider import call_ai

        prompt = f"""You are a UX analyst for IhsanOS, a multi-tenant operations platform
for Islamic institutions (mosques, madrasahs, NGOs).

Here is the last 7 days of UI event data:

{stats}

Based on this data, write a concise UX report with EXACTLY 3 specific, actionable
recommendations ranked by impact. Each recommendation must:
1. Name the specific page/flow and what the data shows
2. Explain WHY it's a problem for the user (mosque admin, cashier, supplier, etc.)
3. Propose a specific UI fix (not vague "improve the experience")

Format as plain text (no markdown — this goes into a Telegram message).
Keep the total report under 200 words. Be specific. Cite the numbers."""

        analysis = await call_ai(
            prompt,
            system="You are a UX analyst. Be specific and actionable. Cite data.",
            model="fast",
            max_tokens=500,
        )

        if not analysis or len(analysis.strip()) < 50:
            return ""

        return f"\U0001f4ca UX Analysis (last 7 days)\n\n{analysis.strip()}"

    except Exception as e:
        logger.error(f"ux_analysis failed: {e}")
        return ""


def _compute_stats(events: list[dict]) -> str:
    """Compute human-readable stats from raw events."""
    lines: list[str] = []

    # Count by event type
    by_type: dict[str, int] = {}
    for e in events:
        name = e.get("event_name", "unknown")
        by_type[name] = by_type.get(name, 0) + 1

    lines.append(f"Total events: {len(events)}")
    lines.append(f"Event breakdown: {', '.join(f'{k}={v}' for k, v in sorted(by_type.items(), key=lambda x: -x[1]))}")
    lines.append("")

    # Flow completion rates
    flow_starts: dict[str, int] = {}
    flow_completes: dict[str, int] = {}
    flow_abandons: dict[str, int] = {}

    for e in events:
        props = e.get("properties") or {}
        flow = props.get("flow")
        if not flow:
            continue

        name = e.get("event_name", "")
        if name == "flow_started":
            flow_starts[flow] = flow_starts.get(flow, 0) + 1
        elif name == "flow_completed":
            flow_completes[flow] = flow_completes.get(flow, 0) + 1
        elif name in ("flow_abandoned", "idle_abandon"):
            flow_abandons[flow] = flow_abandons.get(flow, 0) + 1

    if flow_starts:
        lines.append("Flow completion rates:")
        for flow in sorted(flow_starts.keys()):
            started = flow_starts.get(flow, 0)
            completed = flow_completes.get(flow, 0)
            abandoned = flow_abandons.get(flow, 0)
            rate = (completed / started * 100) if started > 0 else 0
            lines.append(
                f"  {flow}: {started} started, {completed} completed ({rate:.0f}%), "
                f"{abandoned} abandoned"
            )
        lines.append("")

    # Rage clicks — group by element
    rage_clicks: dict[str, int] = {}
    for e in events:
        if e.get("event_name") != "rage_click":
            continue
        props = e.get("properties") or {}
        element = props.get("element", "unknown")
        rage_clicks[element] = rage_clicks.get(element, 0) + 1

    if rage_clicks:
        lines.append("Rage clicks (confusion hotspots):")
        for element, count in sorted(rage_clicks.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {element}: {count} rage events")
        lines.append("")

    # Idle abandons — group by flow + path
    idle_abandons: dict[str, int] = {}
    for e in events:
        if e.get("event_name") != "idle_abandon":
            continue
        props = e.get("properties") or {}
        key = f"{props.get('flow', '?')} on {props.get('path', '?')}"
        idle_abandons[key] = idle_abandons.get(key, 0) + 1

    if idle_abandons:
        lines.append("Idle abandons (where users get stuck):")
        for key, count in sorted(idle_abandons.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {key}: {count} abandon events")
        lines.append("")

    # Top pages by views
    page_views: dict[str, int] = {}
    for e in events:
        if e.get("event_name") != "page_view":
            continue
        props = e.get("properties") or {}
        path = props.get("path", "?")
        page_views[path] = page_views.get(path, 0) + 1

    if page_views:
        lines.append("Top pages by views:")
        for path, count in sorted(page_views.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  {path}: {count} views")

    return "\n".join(lines)
