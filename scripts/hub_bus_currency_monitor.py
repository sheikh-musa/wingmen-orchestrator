#!/usr/bin/env python3
"""hub_bus_currency_monitor.py — DETECT-ONLY: catch the hub's agent-bus going undrained.

WHY (2026-08-19, Nazim #28837 + hub #28847): the hub (cc-orchestrator, VPS) went ~11h
(10:06→21:24Z) WITHOUT reconciling its agent_messages inbox — its DB-driven wake path
(agent_wake_subscriber, VPS-side) wasn't firing, so it only reconciled on operator-channel
turns. The orch_lease heartbeat kept renewing the whole time, so EVERY gauge read healthy
while the bus went undrained — the "healthy-looking trap". The existing priority_sla_watchdog
only tracks P0/P1, so an 11h backlog of P2/P3 render-coordination slipped past infra entirely.

This monitor closes that detection hole until the VPS wake-subscriber is fixed: a cheap,
ANY-PRIORITY check — if the hub has an unread agent_messages row older than STALE_HOURS, it
is not draining its bus → PAGE. Detect-only: it FIRES NO reset and NO nudge (the bus nudge
doesn't wake the hub anyway — that's the defect). It only surfaces the stall to the SRE +
console so the operator-channel poke / VPS fix can happen. Peer of the SLA daemon.

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

    STALE iff there is at least one UNREAD row whose age exceeds stale_hours — that is an
    unambiguous 'the hub is not reading its bus' signal, any priority. An empty inbox is OK
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
    """Return (unread_count, oldest_unread_age_s, oldest_unread_id). Fail-loud on DB error.

    oldest_unread_id is the id of the single oldest-by-created_at unread row (or None if the
    inbox is drained) — the dedupe key for the row-aware snooze."""
    cur.execute(
        "SELECT count(*) OVER (), extract(epoch from (now() - created_at)), id "
        "FROM agent_messages WHERE to_agent=%s AND read_at IS NULL "
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
            what=(f"cc-orchestrator (hub) has {unread_count} unread agent_messages, oldest ~{hrs:.1f}h old, "
                  f"unreconciled past the {STALE_HOURS:.1f}h threshold."),
            why=("The hub's DB-driven wake (agent_wake_subscriber, VPS) isn't firing, so it only reconciles "
                 "on operator-channel turns — its orch_lease heartbeat keeps renewing so every other gauge "
                 "reads healthy while the bus goes undrained (the trap that hid an ~11h latency on 2026-08-19). "
                 "SLA-watchdog is P0/P1-only so it misses P2/P3 hub backlog."),
            do=("Poke the hub via the OPERATOR channel to drain its bus (a bus nudge does NOT wake it — that IS "
                "the defect). Durable fix = the VPS agent_wake_subscriber path (needs VPS access, operator/cai)."),
            detail=f"agent={HUB_AGENT} unread={unread_count} oldest={hrs:.1f}h threshold={STALE_HOURS:.1f}h; DETECT-ONLY.",
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
