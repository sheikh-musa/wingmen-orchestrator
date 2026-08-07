#!/usr/bin/env python3
"""revenue_pipeline — Head of Revenue Phase 1 READ-ONLY digest generator.

Renders the `opportunities` ledger as a weekly pipeline digest: opportunities
grouped by stage, each with its value / next-action / owner / blocker, plus a
"What needs you this week" section surfacing where the OPERATOR is the blocker
(and any hard deadlines or open money/residency gates).

ZERO enforcement, ZERO behavior change. This module ONLY reads the ledger and
formats text/markdown. It does not write, nudge, escalate, send, or act. A
future phase (spec §9 Phase 2/3) makes it a scheduled push + a detect->act
loop; Phase 1 is just the ledger + this reader.

The pure functions (group_by_stage, render_digest, needs_you) take plain dict
rows so they are unit-testable without a database. fetch_opportunities is the
only DB touch and is a plain SELECT.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

# Pipeline order — earliest to latest, then off-pipeline buckets last.
STAGE_ORDER = [
    "lead", "qualified", "scoped", "priced", "proposed", "closing",
    "won", "paid", "delivered", "channel", "parked", "lost",
]

STAGE_LABELS = {
    "lead": "Lead",
    "qualified": "Qualified",
    "scoped": "Scoped",
    "priced": "Priced",
    "proposed": "Proposed",
    "closing": "Closing",
    "won": "Won",
    "paid": "Paid",
    "delivered": "Delivered",
    "channel": "Channels / partners",
    "parked": "Parked",
    "lost": "Lost",
}

# How near a due date must be to flag it in "what needs you this week".
DUE_SOON_DAYS = 60


def _sort_key(opp: Dict[str, Any]) -> Any:
    """Order within a stage: sort_priority asc (NULLs last), then name."""
    sp = opp.get("sort_priority")
    return (sp is None, sp if sp is not None else 0, opp.get("name") or "")


def group_by_stage(opportunities: List[Dict[str, Any]]) -> "list[tuple[str, list]]":
    """Group opportunities by stage, in STAGE_ORDER. Returns a list of
    (stage, [opps]) pairs for non-empty stages only. Unknown stages are
    appended at the end (honest — never silently dropped)."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for opp in opportunities:
        stage = (opp.get("stage") or "lead")
        buckets.setdefault(stage, []).append(opp)

    ordered: List[tuple] = []
    for stage in STAGE_ORDER:
        if stage in buckets:
            ordered.append((stage, sorted(buckets.pop(stage), key=_sort_key)))
    # Any leftover (off-list) stages, alphabetical, so nothing is hidden.
    for stage in sorted(buckets):
        ordered.append((stage, sorted(buckets[stage], key=_sort_key)))
    return ordered


def _fmt_value(opp: Dict[str, Any]) -> str:
    """Human value label: '<model>, ~<value> <ccy>' with graceful nulls."""
    parts = []
    if opp.get("value_model"):
        parts.append(str(opp["value_model"]))
    est = opp.get("est_value")
    if est is not None:
        parts.append(f"~{est:,.0f} {opp.get('currency') or ''}".strip())
    return ", ".join(parts) if parts else "value TBD"


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def needs_you(opportunities: List[Dict[str, Any]], today: Optional[date] = None) -> List[Dict[str, Any]]:
    """The subset that needs the OPERATOR this week: operator-is-blocker, or an
    open money/residency gate, or a hard deadline inside DUE_SOON_DAYS. Ordered
    by sort_priority. Read-only — computes, never acts."""
    today = today or datetime.now(timezone.utc).date()
    out: List[Dict[str, Any]] = []
    for opp in opportunities:
        if opp.get("stage") in ("lost", "delivered"):
            continue
        due = _as_date(opp.get("due"))
        due_soon = due is not None and (due - today).days <= DUE_SOON_DAYS
        if opp.get("operator_is_blocker") or opp.get("gate_status") == "open" or due_soon:
            out.append(opp)
    return sorted(out, key=_sort_key)


def _reason_flags(opp: Dict[str, Any], today: date) -> str:
    flags = []
    if opp.get("operator_is_blocker"):
        flags.append("you are the blocker")
    if opp.get("gate_status") == "open":
        flags.append("open money/residency gate")
    # A surfaced row always shows its deadline (honesty — if it's on your plate,
    # you see when it's due), regardless of the DUE_SOON_DAYS surfacing window.
    due = _as_date(opp.get("due"))
    if due is not None:
        days = (due - today).days
        when = f"due {due.isoformat()}" + (f" ({days}d)" if days >= 0 else " (OVERDUE)")
        flags.append(when)
    return "; ".join(flags)


def render_digest(opportunities: List[Dict[str, Any]], today: Optional[date] = None) -> str:
    """Render the full weekly pipeline digest as markdown. Pure formatting."""
    today = today or datetime.now(timezone.utc).date()
    active = [o for o in opportunities if o.get("stage") not in ("parked", "lost")]

    lines: List[str] = []
    lines.append(f"# Revenue pipeline — week of {today.isoformat()}")
    lines.append("")
    lines.append(
        f"_{len(opportunities)} threads tracked "
        f"({len(active)} active). Read-only ledger snapshot (Head of Revenue Phase 1) — "
        f"no action taken; the operator sequences the forks._"
    )
    lines.append("")

    # Section 1 — the pipeline, grouped by stage.
    lines.append("## Pipeline by stage")
    lines.append("")
    for stage, opps in group_by_stage(opportunities):
        label = STAGE_LABELS.get(stage, stage.capitalize())
        lines.append(f"### {label} ({len(opps)})")
        for opp in opps:
            flag = " ⚠️ you" if opp.get("operator_is_blocker") else ""
            lines.append(f"- **{opp.get('name', opp.get('slug', '?'))}**{flag} — {_fmt_value(opp)}")
            if opp.get("next_action"):
                owner = opp.get("next_action_owner")
                owner_s = f" _({owner})_" if owner else ""
                lines.append(f"    - Next: {opp['next_action']}{owner_s}")
            if opp.get("blocker"):
                lines.append(f"    - Blocker: {opp['blocker']}")
            gate = opp.get("gate_status")
            if gate and gate not in ("none", "n/a"):
                note = opp.get("gate_note")
                lines.append(f"    - Gate ({gate}): {note}" if note else f"    - Gate: {gate}")
        lines.append("")

    # Section 2 — what needs the operator this week.
    ny = needs_you(opportunities, today)
    lines.append("## What needs you this week")
    lines.append("")
    if not ny:
        lines.append("_Nothing is blocked on you right now._")
    else:
        for opp in ny:
            reasons = _reason_flags(opp, today)
            lines.append(f"- **{opp.get('name', opp.get('slug', '?'))}** — {reasons}")
            if opp.get("next_action"):
                lines.append(f"    - {opp['next_action']}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# The only DB touch: a plain read.
# ---------------------------------------------------------------------------
_SELECT = (
    "SELECT slug, name, stage, value_model, est_value, currency, next_action, "
    "next_action_owner, due, owner, blocker, operator_is_blocker, gate_status, "
    "gate_note, partner, sort_priority, notes, created_at, updated_at "
    "FROM opportunities ORDER BY sort_priority NULLS LAST, name"
)


def fetch_opportunities(dsn: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read all ledger rows as dicts. READ-ONLY — a plain SELECT, no writes."""
    import psycopg  # local import so the pure functions need no DB driver

    dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_SELECT)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    from dotenv import load_dotenv

    root = os.path.join(os.path.dirname(__file__), "..")
    load_dotenv(os.path.join(root, ".env"))
    opportunities = fetch_opportunities()
    sys.stdout.write(render_digest(opportunities))
    return 0


if __name__ == "__main__":
    sys.exit(main())
