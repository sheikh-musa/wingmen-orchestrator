#!/usr/bin/env python3
"""hub_bus_currency_monitor.py — DETECT-ONLY: catch the hub's agent-bus going undrained.

WHY (2026-08-19, Nazim #28837 + hub #28847): the hub (cc-orchestrator, VPS) went ~11h
(10:06→21:24Z) WITHOUT reconciling its agent_messages inbox — its DB-driven wake path
(agent_wake_subscriber, VPS-side) wasn't firing, so it only reconciled on operator-channel
turns. The orch_lease heartbeat kept renewing the whole time, so EVERY gauge read healthy
while the bus went undrained — the "healthy-looking trap". The existing priority_sla_watchdog
only tracks P0/P1, so an 11h backlog of P2/P3 render-coordination slipped past infra entirely.

This monitor surfaces a hub bus-drain failure: if a FLOOR-QUALIFYING unread row (CAI-451:
priority P0/P1 AND requires_response — the ONLY class that auto-wakes the hub) is older than
STALE_HOURS, the hub failed to wake+drain on a message that SHOULD have woken it → PAGE.
Detect-only: it FIRES NO reset and NO nudge (a bus nudge doesn't wake the hub — that's the
defect class). It only surfaces the stall to the SRE + console. Peer of the SLA daemon.

⚠️ CAI-451 FLOOR GATE (Nazim #32411, 2026-08-23): originally this was an ANY-PRIORITY check —
but the hub auto-wakes ONLY on P0/P1+rr; below-floor rows (P3, or any non-rr) drain solely on
operator-channel turns BY DESIGN, so a stale P3/non-rr is NOT a wake defect. Alarming on it
was a false positive (a benign P3 sat 27h and paged, sending the console chasing a non-problem).
So the query (_read_hub_bus_state) now counts ONLY floor-qualifying unread. A separate, real
residual (CAI-1303) is the hub-side reconcile-on-wake gap: even a woke:True doorbell may not get
the hub's turn-logic to read its bus — THAT is the class a stale floor-qualifying row now catches.

Deploys INERT: the live page only goes out under HUB_BUS_MONITOR_ENABLED=1 (set on the
launchd). HUB_BUS_MONITOR_DRY=1 prints instead of inserting (test). Deduped so the periodic
daemon never re-pages the same stall within PAGE_COOLDOWN_MIN.
"""
from __future__ import annotations

import os
import sys

HUB_AGENT = "cc-orchestrator"
STALE_HOURS = float(os.environ.get("HUB_BUS_STALE_HOURS", "2.5"))   # unread older than this ⇒ bus-stale
PAGE_COOLDOWN_MIN = int(os.environ.get("HUB_BUS_PAGE_COOLDOWN_MIN", "150"))  # ~one page per stall
# Row-aware snooze: once we've paged on a SPECIFIC oldest-unread row, don't re-page on that
# SAME row for this long (the DB-wake defect can leave a benign row unread for many hours;
# re-paging every PAGE_COOLDOWN on the identical known row is pure noise — Nazim #29208). A
# NEW/different oldest-unread row (a genuinely new undrained backlog, possibly urgent) is NOT
# snoozed — it pages immediately, so detection is preserved.
SNOOZE_SAME_ROW_MIN = int(os.environ.get("HUB_BUS_SNOOZE_SAME_ROW_MIN", "1440"))  # 24h per known row
RECIPIENTS = ("orch-console", "cc-fleet-health")  # Nazim relays to operator; SRE owns the loop
ENABLED = os.environ.get("HUB_BUS_MONITOR_ENABLED") == "1"
DRY = os.environ.get("HUB_BUS_MONITOR_DRY") == "1"
SRE_FROM = "cc-fleet-health"


# ── PURE classification (unit-tested; no DB) ─────────────────────────────────
def classify_bus_currency(unread_count: int, oldest_unread_age_s: "float | None",
                          stale_hours: float = STALE_HOURS) -> "tuple[str, str]":
    """PURE. Decide whether the hub's agent-bus is STALE (undrained past threshold).

    The caller passes counts/age over FLOOR-QUALIFYING unread only (P0/P1+rr — see
    _read_hub_bus_state / the CAI-451 gate), so a positive here means a message that SHOULD
    have auto-woken+drained the hub is stale. STALE iff there is at least one such UNREAD row
    whose age exceeds stale_hours — an unambiguous 'the hub isn't reading its bus on a wake-
    eligible row' signal. An empty inbox is OK
    (nothing to drain — 'quiet' is not a stall; this is why we key on oldest-UNREAD age, not
    last-reconcile age, which can't tell quiet from wedged). A None oldest-age (no unread)
    is OK. Returns (verdict, reason), verdict in {'ok','stale'}."""
    if unread_count <= 0 or oldest_unread_age_s is None:
        return "ok", f"hub inbox drained ({unread_count} unread) — nothing stale"
    thresh_s = stale_hours * 3600.0
    if oldest_unread_age_s > thresh_s:
        return "stale", (f"hub has {unread_count} unread; oldest {oldest_unread_age_s/3600.0:.1f}h "
                         f"> {stale_hours:.1f}h threshold — bus undrained (DB-wake gap)")
    return "ok", (f"hub has {unread_count} unread but oldest {oldest_unread_age_s/3600.0:.1f}h "
                  f"within {stale_hours:.1f}h — normal reconcile latency")


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _read_hub_bus_state(cur):
    """Return (unread_count, oldest_unread_age_s, oldest_unread_id) over ONLY the hub's
    FLOOR-QUALIFYING unread. Fail-loud on DB error.

    ⚠️ CAI-451 FLOOR GATE (Nazim #32411, 2026-08-23 — fixes a false-positive): the hub
    (cc-orchestrator) auto-wakes ONLY on `priority IN ('P0','P1') AND requires_response`
    (is_wake_eligible_recipient, agent_wake.py). Below-floor rows (P3, or any non-rr) are
    NOT auto-woken BY DESIGN — they drain only on operator-channel turns. So a stale P3/non-rr
    unread is NOT a DB-wake defect; alarming on it was the false positive (a benign P3 that sat
    27h made this monitor page and sent Nazim chasing a non-problem). We therefore count only
    floor-qualifying unread: a floor-qualifying row that sits past threshold is the REAL signal
    (it should have auto-woken AND drained — if it didn't, either the doorbell didn't fire or
    the hub isn't reconciling its bus on wake, the residual reconcile gap).

    oldest_unread_id is the id of the single oldest-by-created_at floor-qualifying unread row
    (or None if none) — the dedupe key for the row-aware snooze."""
    cur.execute(
        "SELECT count(*) OVER (), extract(epoch from (now() - created_at)), id "
        "FROM agent_messages WHERE to_agent=%s AND read_at IS NULL "
        "AND priority IN ('P0','P1') AND requires_response = true "  # CAI-451 floor (the only auto-wake class)
        "ORDER BY created_at ASC LIMIT 1", (HUB_AGENT,))
    row = cur.fetchone()
    if row is None:
        return 0, None, None
    n, age, oldest_id = row
    return int(n or 0), (float(age) if age is not None else None), oldest_id


def _already_paged_for_row(cur, oldest_id: int, snooze_min: int) -> bool:
    """Row-aware dedup: have we already paged on THIS specific oldest-unread row within the
    snooze window? A same-row page in [now-snooze, now] ⇒ skip (known benign/stuck row). A new
    oldest_id has no such prior page ⇒ we page (detection of a genuinely new backlog preserved)."""
    cur.execute(
        "SELECT 1 FROM agent_messages WHERE from_agent=%s AND subject LIKE %s "
        "AND created_at > now() - make_interval(mins => %s) LIMIT 1",
        (SRE_FROM, f"HUB BUS-STALE:%[oldest-id={oldest_id}]%", snooze_min))
    return cur.fetchone() is not None


def _page(cur, unread_count: int, oldest_unread_age_s: float, reason: str, oldest_id: int) -> int:
    """NON-DESTRUCTIVE page to RECIPIENTS. Returns count sent. Deduped by the caller.

    The subject carries a stable `[oldest-id={oldest_id}]` marker so the row-aware snooze
    (_already_paged_for_row) can dedupe re-pages on the SAME stuck row while still firing on a
    NEW oldest-unread row."""
    hrs = oldest_unread_age_s / 3600.0
    subject = (f"HUB BUS-STALE: cc-orchestrator has {unread_count} unread, oldest {hrs:.1f}h "
               f"— bus undrained [oldest-id={oldest_id}]")
    try:
        from nervous_system.alert_format import format_alert
        body = format_alert(
            icon="🛰️",
            title="Hub is not draining its agent-bus (DB-wake gap)",
            what=(f"cc-orchestrator (hub) has {unread_count} FLOOR-QUALIFYING (P0/P1+rr) unread, oldest ~{hrs:.1f}h "
                  f"old, past the {STALE_HOURS:.1f}h threshold — a message that SHOULD have auto-woken+drained it."),
            why=("Only P0/P1+rr auto-wake the hub (CAI-451); one is stale, so either the doorbell didn't fire OR "
                 "the hub's turn-logic didn't reconcile its bus on wake (the CAI-1303 reconcile-on-wake gap). Its "
                 "orch_lease heartbeat keeps renewing so every other gauge reads healthy while the bus goes "
                 "undrained (the trap that hid an ~11h latency on 2026-08-19)."),
            do=("VERIFY the doorbell fired (agent_wake auto-wake for this row). If it woke:True but the row stayed "
                "unread past a hub turn = the hub-side reconcile-on-wake gap (hub turn-logic; cai + hub-owner). "
                "Interim: an operator-channel poke drains it (a bus nudge alone won't wake the hub)."),
            detail=f"agent={HUB_AGENT} floor-qualifying-unread={unread_count} oldest={hrs:.1f}h threshold={STALE_HOURS:.1f}h; DETECT-ONLY.",
            ref="HUB-BUS-CURRENCY-MONITOR",
        )
    except Exception:  # noqa: BLE001 — alert_format optional; fall back to a plain body
        body = (f"🛰️ Hub bus-stale: cc-orchestrator has {unread_count} unread, oldest ~{hrs:.1f}h "
                f"(> {STALE_HOURS:.1f}h). {reason}. DB-wake gap; poke via operator channel. DETECT-ONLY.")
    sent = 0
    for to_agent in RECIPIENTS:
        if DRY:
            print(f"[hub-bus-monitor DRY] would page {to_agent} :: {subject}")
        else:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
                "VALUES (%s,%s,'update',%s,%s,false,'P2')", (SRE_FROM, to_agent, subject, body))
        sent += 1
    return sent


def run_once() -> int:
    """One detect pass. Prints the verdict; pages on a deduped stale. Returns 0 (detect-only)."""
    import psycopg
    dsn = _dsn()
    if not dsn:
        print("[hub-bus-monitor] no DATABASE_URL — cannot run", file=sys.stderr)
        return 2
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        unread, oldest, oldest_id = _read_hub_bus_state(cur)
        verdict, reason = classify_bus_currency(unread, oldest)
        print(f"[hub-bus-monitor] {verdict.upper()}: {reason}"
              + (f" (oldest-id={oldest_id})" if oldest_id is not None else ""))
        if verdict == "stale" and ENABLED:
            if _already_paged_for_row(cur, oldest_id, SNOOZE_SAME_ROW_MIN):
                print(f"[hub-bus-monitor] stale but already paged on row #{oldest_id} within "
                      f"{SNOOZE_SAME_ROW_MIN}m — row-snoozed, no page (a NEW oldest row still pages)")
            else:
                sent = _page(cur, unread, oldest, reason, oldest_id)
                if not DRY:
                    c.commit()
                print(f"[hub-bus-monitor{' DRY' if DRY else ''}] paged {sent} recipient(s) on hub bus-stall")
        elif verdict == "stale":
            print("[hub-bus-monitor] stale but HUB_BUS_MONITOR_ENABLED!=1 — INERT (detect-only, no page)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
