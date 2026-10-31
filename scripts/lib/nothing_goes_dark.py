"""nothing_goes_dark.py — the shared "is this item dark?" derivation (bus
77019 Phase 1; reports/nothing-goes-dark-design-20261026/design.md parts
1+5), extracted from nervous_system/console/db.py (op#34349/34354 Phase 2,
docs/superpowers/plans/2026-10-31-nothing-goes-dark-phase2.md Task 1).

Moved so the console's PULLED status board (what Musa sees if he looks) and
the SLA-watchdog's PUSHED party-update-chase net (what fires if he doesn't,
scripts/priority_sla_watchdog.py) consume the exact same overdue definition
and can never diverge on it.

waiting_party / overdue are DERIVED per read, never stored, so they can't go
stale — Phase 1 deliberately shipped NO migration for this (see the design
doc's own part 1 vs. what actually shipped): same doctrine as the asks
board's live status column.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

# Quiet-cadence defaults (seconds) per waiting party — the design doc's open
# "per-party cadence" question answered with a conservative first pass: an
# open item should show movement to the OPERATOR at least daily; a CLIENT
# channel hears from us at least twice a day (their clock is the business's
# reputation, not ours). Tunable in ONE place.
STATUS_CADENCE_S = {"operator": 24 * 3600, "client": 12 * 3600}

# held_commitments.channel_tag values that mean "the OPERATOR is the owed
# party". Every other tag names a client channel — the tag IS the party name
# ('gazzabyte-irsyad', 'cosem-caai', …).
_OPERATOR_CHANNEL_TAGS = {
    None, "", "nazim-console", "tmux-console", "fleet", "orch-channel",
    "operator-orch", "operator",
}


def waiting_party_for_ask(ask_surface) -> str:
    """Open ask → who the fleet owes its next update to. 'client-channel' is
    the only structured client surface today; everything else (incl. NULL /
    unknown) files under the operator — the safe default keeps an item's
    party axis visible instead of guessing a client."""
    return "client" if (ask_surface or "").strip() == "client-channel" else "operator"


def waiting_party_for_commitment(channel_tag) -> Tuple[str, Optional[str]]:
    """Open held commitment → (waiting_party, client_name). Internal console
    tags mean the operator is owed; any other tag names the client channel."""
    tag = (channel_tag or "").strip()
    if tag in _OPERATOR_CHANNEL_TAGS:
        return ("operator", None)
    return ("client", tag)


def _parse_maybe_ts(value):
    """Best-effort parse of a due/last-update value into an AWARE datetime.
    ISO strings, datetimes and date-only strings ('2026-10-25' → that day's
    UTC midnight) parse; anything else returns None — the caller then falls
    through to the safe 'quiet' verdict, so garbage never 500s the page."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def derive_overdue(due_at, last_update_at, waiting_party, now=None) -> Tuple[bool, Optional[str]]:
    """(overdue, reason) for one open item.

    past_due — the item's own due date (hc.due_at / ask chase_by) is behind us.
    quiet    — no due date, but the owed party has heard nothing for longer
               than the party's cadence. A MISSING last-signal counts as
               quiet: "we have never updated them" is the darkest state, not
               an exemption from the clock."""
    now = now or datetime.now(timezone.utc)
    due = _parse_maybe_ts(due_at)
    if due is not None and due <= now:
        return (True, "past_due")
    last = _parse_maybe_ts(last_update_at)
    if last is None:
        return (True, "quiet")
    cadence = STATUS_CADENCE_S.get(waiting_party, STATUS_CADENCE_S["operator"])
    if (now - last).total_seconds() > cadence:
        return (True, "quiet")
    return (False, None)


def annotate_status_rows(rows: List[dict], now=None) -> List[dict]:
    """Attach the derived axes to raw union rows and sort darkest-first:
    overdue above healthy, breaches ('fired') above quiet, then longest
    silence first — the quietest item sits at the very top of the view."""
    now = now or datetime.now(timezone.utc)
    out = []
    for r in rows:
        raw = r.get("waiting_party_raw")
        if r.get("kind") == "commitment":
            party, client_name = waiting_party_for_commitment(raw)
        else:
            party, client_name = waiting_party_for_ask(raw), None
        if r.get("kind") == "commitment" and r.get("status") == "fired":
            # the sweeper already escalated a breach (and keeps re-escalating
            # until discharged) — never let a fresh touch un-dark a breach
            overdue, reason = True, "fired"
        else:
            overdue, reason = derive_overdue(
                r.get("due_at"), r.get("last_update_at"), party, now=now)
        age = r.get("last_update_age_s")
        item = {k: v for k, v in r.items() if k != "waiting_party_raw"}
        item.update({
            "waiting_party": party,
            "client_name": client_name,
            "overdue": overdue,
            "overdue_reason": reason,
            "source": ("held_commitments#" if r.get("kind") == "commitment"
                       else "operator_asks#") + str(r.get("id")),
        })
        out.append(item)
    out.sort(key=lambda r: (
        0 if r["overdue"] else 1,
        0 if r["overdue_reason"] == "fired" else 1,
        -(r["last_update_age_s"] if r["last_update_age_s"] is not None
          else 1 << 30),
    ))
    return out


def build_open_items_query() -> Tuple[str, list]:
    """The OPEN-ITEM UNION behind /api/status-board (bus 77019 Phase 1) and
    the SLA-watchdog's party-update-chase net (Phase 2).

    Asks side: same base facts as build_fleet_asks_query (triage_state='ask',
    closed_at IS NULL, status derived LIVE from the thread), minus the group/
    board_status columns this view doesn't need. Display text follows the
    board's never-raw-captured-text rule: operator_summary (decision-first
    line) → triage_summary → '(awaiting triage)'.

    last_update_at = the latest movement visible to the owed party — the
    newest bus row in the ask's thread, else triage/confirm/creation. Any
    movement resets the clock, INCLUDING the operator's own follow-up: if
    they asked a follow-up nobody answered, the cadence clock runs from
    THEIR message, so the item goes quiet exactly when it should.

    Commitments side: pending OR fired-but-not-discharged (a fired row is a
    live breach the sweeper is still escalating — dropping it here would hide
    precisely the thing this view exists to surface), last touch = updated_at.

    Raw union rows carry `waiting_party_raw` (ask_surface / channel_tag);
    annotate_status_rows derives the axes in Python so the rules stay
    unit-testable and never drift between SQL and page."""
    sql = (
        "WITH latest AS ("
        "  SELECT DISTINCT ON (thread_id) "
        "         thread_id, from_agent, to_agent, requires_response, "
        "         responded_at, read_at, created_at "
        "  FROM agent_messages "
        "  WHERE thread_id IS NOT NULL AND is_test IS NOT TRUE "
        "  ORDER BY thread_id, id DESC"
        ") "
        "SELECT * FROM ("
        "  SELECT 'ask'::text AS kind, "
        "    a.id, "
        "    COALESCE(a.operator_summary, a.triage_summary, '(awaiting triage)') AS title, "
        "    COALESCE(a.delegated_to, 'unassigned') AS owner, "
        "    CASE "
        "      WHEN a.waiting_on_operator OR a.needs_operator_decision   THEN 'waiting_on_musa' "
        "      WHEN a.thread_id IS NULL                                  THEN 'on_nazim' "
        "      WHEN l.from_agent <> 'orch-console' AND l.to_agent = 'orch-console' "
        "           AND l.requires_response AND l.responded_at IS NULL   THEN 'needs_you' "
        "      WHEN l.responded_at IS NOT NULL                           THEN 'delegate_done' "
        "      WHEN l.read_at IS NOT NULL                                THEN 'in_progress' "
        "      ELSE                                                            'pending' "
        "    END AS status, "
        "    a.ask_surface AS waiting_party_raw, "
        "    a.chase_by AS due_at, "
        "    COALESCE(l.created_at, a.triaged_at, a.confirmed_at, a.created_at) AS last_update_at, "
        "    round(extract(epoch FROM (now() - COALESCE(l.created_at, a.triaged_at, a.confirmed_at, a.created_at))))::int AS last_update_age_s "
        "  FROM operator_asks a "
        "  LEFT JOIN latest l ON l.thread_id = a.thread_id "
        "  WHERE a.closed_at IS NULL AND a.triage_state = 'ask' "
        "  UNION ALL "
        "  SELECT 'commitment'::text AS kind, "
        "    h.id, "
        "    h.title, "
        "    h.owner_agent, "
        "    h.status, "
        "    h.channel_tag AS waiting_party_raw, "
        "    h.due_at, "
        "    h.updated_at, "
        "    round(extract(epoch FROM (now() - h.updated_at)))::int AS last_update_age_s "
        "  FROM held_commitments h "
        "  WHERE h.status IN ('pending', 'fired') "
        "    AND h.discharged_at IS NULL "
        "    AND h.cancelled_at IS NULL "
        ") u "
        "ORDER BY last_update_at NULLS FIRST "
        "LIMIT 600"
    )
    return sql, []
