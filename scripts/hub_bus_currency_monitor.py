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


# Session-deaf threshold: the hub silent (no real turn) for longer than this WHILE work waits and
# the host is up = the alive-host/dead-session hole (gzb login-screen, 2026-09-05). Below the
# bus-currency 2.5h so operator-unheard is caught FAST, above a hub's normal inter-turn gap.
SESSION_STALE_MIN = float(os.environ.get("HUB_SESSION_STALE_MIN", "45"))
# The session-deaf PAGE has its OWN arm flag, SEPARATE from HUB_BUS_MONITOR_ENABLED, so this new
# check ships INERT (logs its verdict, never pages) until it's reviewed and armed on the launchd —
# the hub false-DEAD class (451e110) means a hub-liveness pager gets armed deliberately, not on save.
SESSION_DEAF_ENABLED = os.environ.get("HUB_SESSION_DEAF_ENABLED") == "1"


def classify_session_liveness(lease_fresh: bool, last_turn_age_s: "float | None",
                              work_waiting: bool,
                              threshold_s: float = SESSION_STALE_MIN * 60.0) -> "tuple[str, str]":
    """PURE. Catch alive-host / dead-SESSION — the hole the lease/heartbeat CANNOT see.

    On 2026-09-05 the gzb hub's claude sat at the OAuth login screen for 1.5h; orch_lease.renewed_at
    (systemd timer) AND the agent_status heartbeat (boot_orch timer) both stayed FRESH the whole
    time, so every host/lease gauge read healthy while the SESSION reconciled nothing. The only
    signal that saw it was the SESSION's own activity — the hub's last real bus turn.

    verdict:
      'host_down'    — lease NOT fresh: the HOST is down, which is fleet_health_lease reclaim's job,
                       not this check. We stay silent to avoid double-signalling that path.
      'session_deaf' — lease fresh (host up) AND no hub turn in > threshold AND work is waiting.
      'ok'           — turning recently, or genuinely quiet (no work waiting). work_waiting is the
                       quiet-vs-wedged gate: last-turn age ALONE would false-page an idle hub, so we
                       page only when the hub is silent WHILE something it should handle is queued.
    """
    if not lease_fresh:
        return "host_down", "orch_lease not fresh — HOST down (fleet_health_lease reclaim's job, not session-deaf)"
    if last_turn_age_s is not None and last_turn_age_s > threshold_s and work_waiting:
        return "session_deaf", (f"hub host up (lease fresh) but no real turn in "
                                f"{last_turn_age_s/60.0:.0f}m (> {threshold_s/60.0:.0f}m) WHILE work is "
                                f"waiting — alive-host/dead-session (session stuck/deaf)")
    if not work_waiting:
        return "ok", "hub quiet — nothing waiting (idle is not deaf)"
    return "ok", (f"hub turned {(last_turn_age_s or 0)/60.0:.0f}m ago — within "
                  f"{threshold_s/60.0:.0f}m, reconciling")


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


LEASE_FRESH_S = float(os.environ.get("HUB_LEASE_FRESH_S", "900"))  # orch_lease TTL: renewed within = host up


def _read_hub_session_state(cur, threshold_s: float):
    """Return (lease_fresh, last_turn_age_s, work_waiting, detail) for the session-deaf check.

    lease_fresh  — orch_lease renewed within LEASE_FRESH_S (host up; the signal that STAYS fresh
                   via the systemd timer even when the session is dead — hence NOT sufficient alone).
    last_turn_age_s — age of the hub's last REAL bus turn (max(agent_messages.created_at) from the
                   hub); the session-activity signal that actually saw the gzb login-screen stall.
    work_waiting — something the hub should have handled has waited > threshold: a floor-qualifying
                   (P0/P1+rr) unread older than threshold, OR an inbound operator message that
                   arrived AFTER the hub's last turn and is itself older than threshold. This is the
                   quiet-vs-wedged gate (operator handled_at is unreliable — verified 2026-09-05 —
                   so we use inbound created_at vs last-turn, never handled_at)."""
    secs = int(threshold_s)
    cur.execute("SELECT extract(epoch from (now()-renewed_at)) FROM orch_lease WHERE holder=%s", (HUB_AGENT,))
    row = cur.fetchone()
    lease_age = float(row[0]) if row and row[0] is not None else None
    lease_fresh = lease_age is not None and lease_age < LEASE_FRESH_S

    cur.execute("SELECT extract(epoch from (now()-max(created_at))), max(created_at) "
                "FROM agent_messages WHERE from_agent=%s", (HUB_AGENT,))
    row = cur.fetchone()
    last_turn_age_s = float(row[0]) if row and row[0] is not None else None
    last_turn_ts = row[1] if row else None

    cur.execute("SELECT EXISTS(SELECT 1 FROM agent_messages WHERE to_agent=%s AND read_at IS NULL "
                "AND priority IN ('P0','P1') AND requires_response "
                "AND created_at < now() - make_interval(secs => %s))", (HUB_AGENT, secs))
    floor_waiting = bool(cur.fetchone()[0])

    op_waiting = False
    if last_turn_ts is not None:
        cur.execute("SELECT EXISTS(SELECT 1 FROM operator_messages WHERE direction='inbound' "
                    "AND created_at > %s AND created_at < now() - make_interval(secs => %s))",
                    (last_turn_ts, secs))
        op_waiting = bool(cur.fetchone()[0])

    work_waiting = floor_waiting or op_waiting
    detail = (f"lease_age={lease_age:.0f}s(fresh={lease_fresh}) "
              f"last_turn={(last_turn_age_s or -1)/60.0:.0f}m floor_wait={floor_waiting} op_wait={op_waiting}"
              if lease_age is not None else
              f"lease_age=NONE last_turn={(last_turn_age_s or -1)/60.0:.0f}m "
              f"floor_wait={floor_waiting} op_wait={op_waiting}")
    return lease_fresh, last_turn_age_s, work_waiting, detail


def _already_paged_session_deaf(cur, cooldown_min: int) -> bool:
    cur.execute("SELECT 1 FROM agent_messages WHERE from_agent=%s AND subject LIKE 'HUB SESSION-DEAF:%%' "
                "AND created_at > now() - make_interval(mins => %s) LIMIT 1", (SRE_FROM, cooldown_min))
    return cur.fetchone() is not None


def _page_session_deaf(cur, last_turn_age_s: float, reason: str) -> int:
    """NON-DESTRUCTIVE P1 page: the hub host is up but its SESSION is deaf. This is the hole the
    lease/heartbeat cannot see — the operator can be unheard while every host gauge reads green."""
    mins = (last_turn_age_s or 0) / 60.0
    subject = (f"HUB SESSION-DEAF: cc-orchestrator host UP but no real turn in {mins:.0f}m while "
               f"work waits — alive-host/dead-session")
    try:
        from nervous_system.alert_format import format_alert
        body = format_alert(
            icon="🕳️",
            title="Hub host is up but its Claude SESSION is deaf (alive-host/dead-session)",
            what=(f"cc-orchestrator's orch_lease + heartbeat are FRESH (host up) but it has made no real "
                  f"bus turn in ~{mins:.0f}m while wake-eligible / operator work is waiting. {reason}."),
            why=("Both the lease (gzb systemd timer) and the heartbeat (boot_orch timer) renew independently "
                 "of the claude session, so they stay green even when the session is stuck (e.g. the "
                 "2026-09-05 gzb OAuth login-screen stall that left the operator unheard 1.5h). The session's "
                 "own last-turn age is the only signal that sees this; fleet_health_lease reclaim can't (the "
                 "lease never expires)."),
            do=("Check the hub session directly: reach gzb via wingmen-core split-tunnel "
                "(ssh wingmen-core -> sudo gzb-vpn.sh up -> sudo -u wingmen ssh gzb) and look for a login/"
                "auth prompt or a wedged pane; a GENTLE wake or re-auth, NOT a reset. Operator may be unheard."),
            detail=f"agent={HUB_AGENT} last_turn={mins:.0f}m threshold={SESSION_STALE_MIN:.0f}m; DETECT-ONLY.",
            ref="HUB-SESSION-DEAF (hub-bus-currency-monitor)",
        )
    except Exception:  # noqa: BLE001 — alert_format optional
        body = (f"🕳️ Hub session-deaf: cc-orchestrator host up (lease+hb fresh) but no real turn in "
                f"~{mins:.0f}m while work waits. {reason}. Check gzb session for a login/wedged pane; "
                f"gentle wake, not a reset. DETECT-ONLY.")
    sent = 0
    for to_agent in RECIPIENTS:
        if DRY:
            print(f"[hub-session DRY] would page {to_agent} :: {subject}")
        else:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
                "VALUES (%s,%s,'update',%s,%s,false,'P1')", (SRE_FROM, to_agent, subject, body))
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

        # ── SESSION-DEAF check (alive-host/dead-session; Nazim 37812) — complements the
        #    bus-currency check above: that keys on unread-AGE (slow drain), this keys on the
        #    hub's own last-turn age (session stuck) so operator-unheard is caught fast. ──
        lease_fresh, last_turn_age_s, work_waiting, sdetail = _read_hub_session_state(cur, SESSION_STALE_MIN * 60.0)
        sverdict, sreason = classify_session_liveness(lease_fresh, last_turn_age_s, work_waiting)
        print(f"[hub-session] {sverdict.upper()}: {sreason} :: {sdetail}")
        if sverdict == "session_deaf" and SESSION_DEAF_ENABLED:
            if _already_paged_session_deaf(cur, PAGE_COOLDOWN_MIN):
                print(f"[hub-session] session-deaf but already paged within {PAGE_COOLDOWN_MIN}m — cooled, no re-page")
            else:
                sent = _page_session_deaf(cur, last_turn_age_s, sreason)
                if not DRY:
                    c.commit()
                print(f"[hub-session{' DRY' if DRY else ''}] paged {sent} recipient(s) on hub session-deaf")
        elif sverdict == "session_deaf":
            print("[hub-session] session-deaf but HUB_SESSION_DEAF_ENABLED!=1 — INERT (detect-only, no page)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
