"""alert_format — ELI5 Telegram alert helper.

Per Musa 2026-05-03: substrate alerts default to ELI5 mode. Operator opens
Telegram on a phone, scans the alert, and needs to understand within 5
seconds: what's wrong, why it matters, what action (if any) to take.

Convention:

    <icon> <title>

    What: <one sentence in plain English>
    Why it matters: <one sentence — operator-facing consequence>
    What to do: <one sentence — concrete next step or "no action needed">

    Detail: <technical fields for follow-up; optional>
    Ref: <decision_ref or doc link; optional>

Pre-existing alert prose was technical-first (decision-ref names,
substrate jargon, SQL hints) — useful for cc-* agents reading the
notification_log post-hoc but not for at-a-glance Telegram triage.

Use `format_alert` from agent_watchdog + orch_self_audit + deploy_verifier
+ any future substrate alert surface.
"""
from __future__ import annotations


def format_alert(
    *,
    icon: str = "⚠️",
    title: str,
    what: str,
    why: str,
    do: str,
    detail: str | None = None,
    ref: str | None = None,
) -> str:
    """Format a substrate alert in ELI5 mode.

    Args:
        icon: Leading emoji. Default ⚠️ (warning). Use 🚨 for P1, 📋 for FYI.
        title: Plain-language headline (≤60 chars). NO acronyms or decision-refs.
        what: One sentence — the observation in plain English.
        why: One sentence — the operator-facing consequence.
        do: One sentence — the concrete next action, or "No action needed."
        detail: Optional technical context for post-hoc forensics
                (file:line, query, error code). Goes after a blank line.
        ref: Optional decision_ref / doc path for deep-dive context.
    """
    lines = [f"{icon} {title}", ""]
    lines.append(f"What: {what}")
    lines.append(f"Why it matters: {why}")
    lines.append(f"What to do: {do}")
    if detail:
        lines.append("")
        lines.append(f"Detail: {detail}")
    if ref:
        lines.append(f"Ref: {ref}")
    return "\n".join(lines)
