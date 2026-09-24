#!/usr/bin/env python3
"""wake_backstop_sweep — the reliability FLOOR under the realtime wake doorbell
(op#11297, cc-quality spec #16827/#16847).

WHY: the shared realtime subscriber (agent_wake_subscriber) is a single delivery
path, and on 2026-08-08 it stalled SILENTLY ~7.5h (WS is_connected=True, zero
deliveries, no replay) — 41 directed rows, incl P1s, never woke their recipients.
A realtime doorbell that can go deaf needs a periodic backstop that does not depend
on it. This sweep re-wakes any recipient with a directed row rotting unread.

BROADER PREDICATE THAN REALTIME (the cc-quality #16847 correction — do NOT reuse
should_auto_wake here): realtime's should_auto_wake only fires for urgent-NOW rows
(actionable type / requires_response / P0-P1), so a passive `update`/rr=false to a
live lane (the #16838 miss) is correctly not realtime-urgent — yet it must not rot
unread. So the sweep's job is "no directed message rots unread": ANY unread, un-
skipped, non-test, non-P3 directed row past a grace, to an eligible recipient. The
RECIPIENT policy is SHARED with realtime (agent_wake.is_wake_eligible_recipient —
never cc-orchestrator/operator); only the TRIGGER is broader. That shared-recipient/
broader-trigger split is the whole point — see agent_wake.should_auto_wake.

SAFE BY CONSTRUCTION: it calls agent_wake.wake_agent(), which enforces the shared
45s debounce + 5/5min cap (scripts/.agent_wake/*.json) and a busy/mid-turn skip, so
realtime + sweep are ONE limiter (no double-wake, no spam). It pokes only while a
row stays unread and goes quiet the instant read_at is set (no loop). Stateless /
restart-safe. Honors AUTO_WAKE_ENABLED. One instance per host (Mini + VPS-for-hub).

NOT YET (follow-on): offline-while-alive robustness under launchd (cc-quality
acceptance #9) — resolve_tmux_session filters status<>'offline'; a body that self-
marks offline while its pane is alive rides only the fragile pgrep fallback. That
fix keys eligibility on a LIVE session, not the status field, and folds into Part-4
self-registration + fix-3. This sweep delivers the #16838 class (online body,
passive row) now; #9 is flagged, not yet closed.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone

import psycopg

# import (not re-encode) the shared policy + wake primitive
from agent_wake import (  # noqa: E402  (same-dir module; nervous_system on sys.path at runtime)
    _pane_busy,
    auto_wake_enabled,
    resolve_tmux_session,
    should_backstop_wake,
    wake_agent,
)

_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
WAKE_SWEEP_SEC = int(os.environ.get("WAKE_SWEEP_SEC", "60"))     # cadence
WAKE_SWEEP_GRACE_S = int(os.environ.get("WAKE_SWEEP_GRACE_S", "90"))  # let realtime win first

# BACKOFF (Nazim 37509/37512): a stuck row must not be re-woken forever (the existing
# 5/5min cap in wake_agent only RATE-LIMITS it; it never gives up). Two give-ups —
# (A) a target that resolves to NO live session (dead/unreachable — the correct pane-
#     liveness signal, NOT a status/heartbeat field, so a wakeable on-demand body is
#     never false-rotted) is quiesced + escalated ONCE; kills the dead-agent class.
# (B) a row unread past CAP_AGE (~= grace + N*cadence — an age proxy for ~N pokes, since
#     there is no per-row counter and wake state is per-agent) is quiesced + escalated ONCE.
# `skipped_at` IS the once-guard: setting it excludes the row from EVERY future sweep, so
# each row escalates exactly once. No new state/column (agent_messages has no escalated_at;
# skipped_at alone suffices). Escalation is a single page to the operator-ops body.
WAKE_SWEEP_CAP_N = int(os.environ.get("WAKE_SWEEP_CAP_N", "5"))
WAKE_SWEEP_CAP_AGE_S = int(os.environ.get(
    "WAKE_SWEEP_CAP_AGE_S", str(WAKE_SWEEP_GRACE_S + WAKE_SWEEP_CAP_N * WAKE_SWEEP_SEC)))
_ESCALATE_TO = os.environ.get("WAKE_SWEEP_ESCALATE_TO", "orch-console")

# STUCK-PAGE age (Nazim #43063): (B) live-stuck must PAGE only when a row is genuinely stuck, NOT
# at cap_age — a lane mid-task for ~7 min is normal latency, and paging then trains the operator to
# ignore the page. So we QUIESCE (stop re-poking) at cap_age but only ESCALATE a row that is STILL
# unread this long after it was sent. Much longer than cap_age (~6.5m) on purpose.
STUCK_PAGE_AGE_S = int(os.environ.get("WAKE_SWEEP_STUCK_PAGE_AGE_S", "1800"))  # 30 min
# UPPER bound on the stuck-page scan (Nazim #43073): the ("stuck", row_id) once-guard is IN-MEMORY,
# so every daemon restart (deploy / KeepAlive / reboot) would otherwise re-page EVERY quiesced-but-
# unread row however old — a page burst that grows over time. A row stuck longer than this has
# already paged once in some daemon's life, or is a dead-foreign/decision problem, not a NEW page.
STUCK_PAGE_MAX_AGE_S = int(os.environ.get("WAKE_SWEEP_STUCK_PAGE_MAX_AGE_S", "86400"))  # 24 h

# DEAD-FOREIGN gone-window (Nazim #42994 (2), amend B): an agent is "gone on every host" only
# if NO matching agent_status row (base-inclusive) has a heartbeat fresher than this. Much
# longer than fleet_health STALE_MIN (30m) ON PURPOSE — a dead heartbeat DAEMON is not a dead
# PANE, and quiescing a live-but-stale lane silently drops its directed message.
GONE_WINDOW_S = int(os.environ.get("WAKE_SWEEP_GONE_WINDOW_S", "7200"))  # 2h

# CROSS-HOST liveness (Nazim #42994 (2) — replaces the old per-instance HOST-OWNERSHIP SCOPE).
# resolve_tmux_session enumerates only THIS host's panes, but the sweep runs one-instance-per-
# host against the SHARED substrate DB. So a live CROSS-HOST agent resolves to "no live session"
# on the wrong host — and quiescing it there silently drops a deliverable message AND defeats the
# owning host's sweep via the shared DB. The old fix scoped dead-inference to an instance's OWNED
# agents (WAKE_SWEEP_OWNED_AGENTS / a Mini-owns-all-but-hub default). That is now REPLACED: the
# dead-foreign quiesce reads the SUBSTRATE (agent_status heartbeats on EVERY host, base-inclusive
# — see _matching_hbs), so ANY instance can safely quiesce a genuinely-gone agent and no per-
# instance ownership env is needed. Cross-host liveness comes from the shared DB, not from "whose
# pane can I see". The hub (cc-orchestrator) is the one body whose liveness is a lease not a
# heartbeat, so it keeps a dedicated belt (_alive_elsewhere -> _default_hub_lease_fresh).
HUB_AGENT = "cc-orchestrator"          # the one cross-host body whose liveness is the orch_lease

# Broader-than-realtime row predicate. NULL-safe: a NULL is_test counts as not-test,
# a NULL priority counts as not-P3 (still swept). read_at/​skipped_at gate the quiesce.
_SWEEP_SQL = """
    SELECT id, to_agent, message_type, requires_response, priority, is_test, created_at
    FROM agent_messages
    WHERE read_at IS NULL
      AND skipped_at IS NULL
      AND is_test IS NOT TRUE
      AND priority IS DISTINCT FROM 'P3'
      AND created_at < now() - make_interval(secs => %s)
    ORDER BY to_agent, created_at
"""

# STUCK-PAGE predicate (Nazim #43063): rows already QUIESCED (skipped_at NOT NULL) but STILL unread
# this long after send. skipped_at is taken by the quiesce, so the once-guard for THIS page is a
# separate process-level seen-set keyed by row id (below), not skipped_at.
_STUCK_SQL = """
    SELECT id, to_agent, message_type, requires_response, priority, is_test, created_at
    FROM agent_messages
    WHERE read_at IS NULL
      AND skipped_at IS NOT NULL
      AND is_test IS NOT TRUE
      AND priority IS DISTINCT FROM 'P3'
      AND created_at < now() - make_interval(secs => %s)
      AND created_at > now() - make_interval(secs => %s)
    ORDER BY to_agent, created_at
"""


# ── row accessors: rows are tuples (id, to_agent, message_type, requires_response,
#    priority, is_test[, created_at]) per _SWEEP_SQL, or mappings with those keys. Index-
#    based so a legacy 6-tuple (no created_at) and a 7-tuple both work unchanged. ──
def _rf(r, idx, key):
    if isinstance(r, dict):
        return r.get(key)
    return r[idx] if idx < len(r) else None


def _row_id(r):     return _rf(r, 0, "id")
def _to_agent(r):   return _rf(r, 1, "to_agent")
def _created_at(r): return _rf(r, 6, "created_at")


def is_capped(r, now_dt, cap_age_s: int) -> bool:
    """(B) True iff the row has been unread longer than the re-wake cap (age is the proxy
    for ~N pokes — there is no per-row counter, and wake state is per-agent). Unknown age
    (no created_at) → NOT capped: fail toward keeping the backstop, never toward silently
    quiescing a row we cannot age."""
    ca = _created_at(r)
    if ca is None:
        return False
    try:
        return (now_dt - ca).total_seconds() >= cap_age_s
    except Exception:  # noqa: BLE001 — tz/type surprise → treat as un-ageable (not capped)
        return False


def eligible_recipients(rows) -> list[str]:
    """Pure: the deduped, order-stable set of recipients to wake. The SQL is a coarse
    prefilter; the AUTHORITATIVE per-row decision is agent_wake.should_backstop_wake
    (canonical policy, never forked into SQL — cc-quality #16848), applied here as
    defense-in-depth (a row is dropped if it fails the canonical gate even if the SQL
    let it through)."""
    out: list[str] = []
    for r in rows:
        to_agent = _to_agent(r)
        mt, rr, prio, istest = (_rf(r, 2, "message_type"), _rf(r, 3, "requires_response"),
                                _rf(r, 4, "priority"), _rf(r, 5, "is_test"))
        if should_backstop_wake(to_agent, mt, rr, prio, istest) and to_agent not in out:
            out.append(to_agent)
    return out


def _fetch_rows(grace_s: int):
    if not _DSN:
        raise RuntimeError("wake_backstop_sweep: no DATABASE_URL/SUPABASE_DB_URL")
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute(_SWEEP_SQL, (grace_s,))
        return cur.fetchall()


def _fetch_stuck_rows(stuck_page_age_s: int, stuck_page_max_age_s: int = STUCK_PAGE_MAX_AGE_S):
    """Rows QUIESCED but STILL unread in the window [stuck_page_age_s, stuck_page_max_age_s] — the
    (B) stuck-page candidates. The UPPER bound stops a restart from re-paging very old rows."""
    if not _DSN:
        raise RuntimeError("wake_backstop_sweep: no DATABASE_URL/SUPABASE_DB_URL")
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute(_STUCK_SQL, (stuck_page_age_s, stuck_page_max_age_s))
        return cur.fetchall()


def _default_pane_state(agent) -> str:
    """Best-effort 'busy'/'idle'/'unknown' for the escalation text so the operator can tell a
    genuinely-stuck lane from a merely-busy one at a glance (Nazim #43063). Never raises."""
    try:
        sess = resolve_tmux_session(agent)
        if not sess:
            return "no-live-pane"
        return "busy" if _pane_busy(sess) else "idle"
    except Exception:  # noqa: BLE001 — pane read is advisory, never break the sweep
        return "unknown"


def _mark_skipped(row_ids) -> list:
    """Set skipped_at on the given rows via CAS (`WHERE skipped_at IS NULL`) and RETURN the
    ids actually set. skipped_at quiesces the rows (the SQL excludes skipped_at IS NOT NULL)
    AND is the once-guard (a skipped row is never re-fetched → never re-escalated). The
    RETURNING makes it a true CAS: only the instance whose UPDATE wins gets the ids back, so
    a concurrent second sweeper (shared DB, one-per-host) sees an empty return and does NOT
    double-escalate. As cc-fleet-health (identity set for the row trigger). [] on empty."""
    ids = [i for i in (row_ids or []) if i is not None]
    if not ids or not _DSN:
        return []
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute("UPDATE agent_messages SET skipped_at=now() "
                    "WHERE id = ANY(%s) AND skipped_at IS NULL RETURNING id", (ids,))
        got = [r[0] for r in cur.fetchall()]
        conn.commit()
        return got


def _matching_hbs(agent) -> list:
    """last_heartbeat of every agent_status row matching `agent` — BASE-INCLUSIVE
    (agent_id = agent OR base_agent_id = agent). agent_status is keyed by INSTANCE id with the
    base in base_agent_id (cc-substrate-1 / base cc-substrate), while bus rows address the BASE
    id — so a bare agent_id match would find NO row for a live base-addressed lane and call it
    dead (Nazim amend A). Match is EXACT equality, never a 'cc-cosem%' prefix (a live sibling
    cc-cosem-adcda must not spare a dead cc-cosem-platform)."""
    if not _DSN:
        return []
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT last_heartbeat FROM agent_status "
                    "WHERE agent_id=%s OR base_agent_id=%s", (agent, agent))
        return [r[0] for r in cur.fetchall() if r[0] is not None]


def _desired_state_of(agent):
    """fleet_lanes.desired_state for `agent`, matched by lane OR base_agent_id. This is operator
    INTENT, never liveness (mig003: 'liveness is derived on read, never stored') — used ONLY as
    a veto (never quiesce a wanted-up lane). Returns the single desired_state, or None if the
    agent has no lane row. RAISES on an ambiguous match (>1 distinct value) so the caller fails
    CLOSED — an unprovable veto must never license a quiesce (Nazim amend C)."""
    if not _DSN:
        return None
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT desired_state FROM fleet_lanes "
                    "WHERE lane=%s OR base_agent_id=%s", (agent, agent))
        vals = [r[0] for r in cur.fetchall()]
    if len(vals) > 1:
        raise ValueError(f"ambiguous desired_state for {agent}: {vals}")
    return vals[0] if vals else None


def _default_hub_lease_fresh() -> bool:
    """Hub (cc-orchestrator) liveness is orch_lease freshness, NOT a heartbeat (deploy note /
    the 451e110 cross-host class). FAIL-SAFE: if the lease check errors, treat the hub as alive
    so a broken check never false-quiesces the singleton hub."""
    try:
        import singleton_liveness  # same-dir at runtime
        return bool(singleton_liveness.hub_lease_fresh())
    except Exception:  # noqa: BLE001 — unknown hub-lease state fails toward "alive" (never quiesce hub)
        return True


def _base_of(agent):
    """The BASE id of a possibly-instance-addressed id (Nazim bus 43009(b)). PREFER the gone
    instance's OWN agent_status row (its base_agent_id) when a row exists; fall back to stripping
    a trailing -<digits> only when there is no row. Returns None if neither yields a distinct
    base. Lets a row to a DEAD instance of a LIVE base be re-addressed, not false-quiesced."""
    if _DSN:
        try:
            with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
                cur.execute("SELECT base_agent_id FROM agent_status "
                            "WHERE agent_id=%s AND base_agent_id IS NOT NULL LIMIT 1", (agent,))
                row = cur.fetchone()
            if row and row[0] and row[0] != agent:
                return row[0]
        except Exception:  # noqa: BLE001 — fall through to the suffix strip
            pass
    m = re.match(r"^(.+)-\d+$", agent or "")
    return m.group(1) if (m and m.group(1) != agent) else None


# Process-level once-guard for the NON-quiescing escalations (re-address + lookup-failed), which
# have no skipped_at to lean on (Nazim bus 43009(b)). The sweep is a long-lived `while True` loop
# under launchd KeepAlive, so this set survives between sweeps and resets only on a restart — at
# most one repeat page per restart per host. The DURABLE version belongs to root A #3 (budget
# persistence), NOT here; do not build anything heavier.
_ESCALATED_SEEN: set = set()


def _escalate_operator(subject: str, body: str) -> None:
    """One page to the operator-ops body (default orch-console) about a rotting/dead row.
    Best-effort: a page failure must never crash the sweep floor (KeepAlive re-runs)."""
    if not _DSN:
        return
    try:
        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body) "
                "VALUES ('cc-fleet-health',%s,'blocker','P2',%s,%s)", (_ESCALATE_TO, subject, body))
            conn.commit()
    except Exception as e:  # noqa: BLE001 — page best-effort
        print(f"wake_backstop_sweep: escalation page failed ({e})", file=sys.stderr, flush=True)


def sweep_once(*, grace_s: int = WAKE_SWEEP_GRACE_S, rows=None, wake=wake_agent,
               mark=_mark_skipped, escalate=_escalate_operator,
               matching_hbs=_matching_hbs, desired_state_of=_desired_state_of,
               base_of=_base_of, hub_lease_fresh=_default_hub_lease_fresh,
               pane_state=_default_pane_state, stuck_rows=None,
               stuck_page_age_s: int = STUCK_PAGE_AGE_S,
               stuck_page_max_age_s: int = STUCK_PAGE_MAX_AGE_S,
               escalated_seen=None, gone_window_s: int = GONE_WINDOW_S,
               cap_age_s: int = WAKE_SWEEP_CAP_AGE_S, now: float | None = None,
               now_dt=None, dry_run: bool = False) -> dict:
    """One pass. FRESH (under-cap) rotting rows drive a wake of each eligible recipient (the
    doorbell backstop). A row PAST cap_age_s is a give-up candidate. ONLY backstop-eligible
    recipients (should_backstop_wake — never a human/operator or a P3/test row) are classified;
    each capped agent falls into exactly one class (Nazim #42994 (2) + amendments A-D, bus 43007/9):

      LIVE-STUCK (B, split per Nazim #43063) — the agent is ALIVE (a base-inclusive matching
        heartbeat fresher than gone_window_s, or a fresh hub lease) but a row is past cap → QUIESCE
        its capped rows via CAS (stop re-poking), but do NOT page here — a ~7min-unread row is
        normal latency, not stuck. Paging is DEFERRED to the STUCK-PAGE pass below.

      STUCK-PAGE (B page half) — a row QUIESCED but STILL unread past stuck_page_age_s (default
        30m) to a LIVE agent → escalate ONCE (pane state in the text). Once-guard = `seen` keyed
        ("stuck", row_id), since skipped_at is already taken by the quiesce.

      RE-ADDRESS — the agent is a DEAD INSTANCE of a LIVE base (base_of resolves a base with a
        live instance) → escalate ONCE "re-address to <base>", do NOT quiesce (the message is
        still deliverable to the base). Once-guarded (no skipped_at to lean on).

      DEAD-FOREIGN — gone on EVERY host (no base-inclusive heartbeat < gone_window_s, hub lease
        not fresh, and no live base) AND desired_state is not 'up' → quiesce all its capped rows
        via CAS + escalate ONCE naming agent, ids, evidence. Any instance may do this; the CAS
        (mark returns only the ids IT set) makes it exactly-once. Clearing skipped_at re-delivers.

      VETOED (amend C) — gone but desired_state='up' → NOT quiesced (a wanted-up-but-dark lane is
        a singleton/lane-watchdog page, never a delivery-drop here).

      LOOKUP-FAILED (amend C fail-closed) — the desired_state veto lookup errored/ambiguous →
        NEVER quiesce on an unprovable check; escalate for a human. Once-guarded.

    The two NON-quiescing escalations (re-address, lookup-failed) share one process-level guard
    keyed (kind, agent) — see _ESCALATED_SEEN. Injectable deps make it unit-testable without
    DB/tmux; dry_run classifies but mutates nothing (honors AUTO_WAKE_ENABLED)."""
    if rows is None:
        rows = _fetch_rows(grace_s)
        if stuck_rows is None:  # production: fetch both. A test that injects `rows` but not
            stuck_rows = _fetch_stuck_rows(stuck_page_age_s, stuck_page_max_age_s)  # → [] below.
    now_dt = now_dt if now_dt is not None else datetime.now(timezone.utc)
    seen = _ESCALATED_SEEN if escalated_seen is None else escalated_seen

    # A capped row never drives a wake; it is a give-up candidate handled below.
    capped = [r for r in rows if is_capped(r, now_dt, cap_age_s)]
    fresh = [r for r in rows if not is_capped(r, now_dt, cap_age_s)]

    targets = eligible_recipients(fresh)
    # Per-row ceiling (Nazim #43063): key each agent's wake to a STABLE representative row —
    # its oldest (min id) fresh unread row. If that same stale row keeps driving the wake,
    # lane_nudge's per-row ceiling bounds re-delivery across successive sweeps (the storm
    # was one row re-woken ~12x/13min within the per-agent cap). Stable id => consistent key.
    rep_row: dict = {}
    for r in fresh:
        a = _to_agent(r); rid = _row_id(r)
        if rid is None:
            continue
        if a not in rep_row or rid < rep_row[a]:
            rep_row[a] = rid
    results = {a: wake(a, reason="backstop-sweep", dry_run=dry_run, now=now, row_id=rep_row.get(a))
               for a in targets}
    woke = [a for a, r in results.items() if isinstance(r, dict) and r.get("woke")]
    unreachable = sorted(  # observability: fresh-row agents with no local live pane this pass
        a for a, r in results.items()
        if isinstance(r, dict) and r.get("why") == "no live session")

    # CAPPED rows — ONLY backstop-eligible recipients may be classified (a human/operator or a
    # P3/test row is never a "dead agent"). Same canonical predicate as the wake path.
    capped_by_agent: dict = {}
    for r in capped:
        if should_backstop_wake(_to_agent(r), _rf(r, 2, "message_type"), _rf(r, 3, "requires_response"),
                                _rf(r, 4, "priority"), _rf(r, 5, "is_test")):
            capped_by_agent.setdefault(_to_agent(r), []).append(_row_id(r))

    def _alive(agent) -> bool:  # base-inclusive fresh heartbeat, or (for the hub) a fresh lease
        for hb in (matching_hbs(agent) or []):
            try:
                if (now_dt - hb).total_seconds() < gone_window_s:
                    return True
            except Exception:  # noqa: BLE001 — an un-ageable heartbeat is not proof of life
                continue
        if agent == HUB_AGENT:  # the hub's liveness is its lease, not a heartbeat
            try:
                return bool(hub_lease_fresh())
            except Exception:  # noqa: BLE001 — fail SAFE for the singleton (never false-quiesce)
                return True
        return False

    def _escalate_once(kind: str, agent: str, subject: str, body: str) -> bool:
        # once-guard for the NON-quiescing escalations (re-address, lookup-failed) — they set no
        # skipped_at, so without this they page every sweep. Keyed (kind, agent).
        if (kind, agent) in seen:
            return False
        escalate(subject, body)
        seen.add((kind, agent))
        return True

    dead_foreign, live_stuck, readdress, vetoed, lookup_failed = [], [], [], [], []
    escalations: list[dict] = []
    for agent, ids in capped_by_agent.items():
        if _alive(agent):  # (B) alive → QUIESCE ONLY (stop re-poking). Paging is DEFERRED to the
            # stuck-page pass at stuck_page_age_s: a lane mid-task for ~7 min is NORMAL latency, not
            # stuck, so escalating at cap_age (~6.5m) false-pages the operator (Nazim #43063). The
            # row stays read_at IS NULL, so the lane's own reconcile still drains it.
            if dry_run:
                live_stuck.append(agent)
                continue
            newly = mark(ids)
            if newly:
                live_stuck.append(agent)
            continue
        base = base_of(agent)  # gone: a dead INSTANCE of a LIVE base? -> re-address, don't quiesce
        if base and base != agent and _alive(base):
            readdress.append(agent)
            if not dry_run and _escalate_once(
                    "readdress", agent,
                    f"[wake-backstop] rows to dead instance {agent} — re-address to base {base}",
                    f"TL;DR: {len(ids)} row(s) to {agent} (ids={ids}) are past the re-wake cap and {agent} "
                    f"has no live pane, but its BASE {base} IS alive on some host. I did NOT quiesce (the "
                    f"message is still deliverable to {base}). ACTION: re-address these rows to {base}."):
                escalations.append({"kind": "readdress", "agent": agent, "base": base, "ids": ids})
            continue
        try:
            ds = desired_state_of(agent)
        except Exception as e:  # noqa: BLE001 — amend C fail-closed: unprovable veto never quiesces
            lookup_failed.append(agent)
            if not dry_run and _escalate_once(
                    "lookup-failed", agent,
                    f"[wake-backstop] can't verify {agent} before quiescing — needs a human",
                    f"TL;DR: {len(ids)} row(s) to {agent} (ids={ids}) are past the re-wake cap and it looks "
                    f"gone (no live heartbeat on any host), but the desired_state veto lookup FAILED "
                    f"({e!r}) — so I will NOT quiesce (that could silently drop a message to a live lane). "
                    f"Left unread. ACTION: check {agent}'s registry row."):
                escalations.append({"kind": "lookup-failed", "agent": agent, "ids": ids})
            continue
        if str(ds or "").lower() == "up":
            vetoed.append(agent)                         # amend C veto — keep deliverable
            continue
        if dry_run:
            dead_foreign.append(agent)                   # would-quiesce (observe-only)
            continue
        newly = mark(ids)                                # CAS: only the ids we actually set
        if newly:                                        # escalate ONLY if we won the CAS
            dead_foreign.append(agent)
            escalate(
                f"[wake-backstop] directed rows rotting to dead agent {agent} — quiesced once",
                f"TL;DR: {len(newly)} directed row(s) to {agent} (ids={newly}) are past the "
                f"re-wake cap AND {agent} is GONE on every host — no agent_status heartbeat "
                f"(base-inclusive) fresher than {gone_window_s}s; desired_state={ds!r} (not up). "
                f"Re-poking a dead agent forever is pointless, so I quiesced them (set skipped_at) "
                f"+ escalated ONCE. They are still read_at IS NULL in {agent}'s inbox. ACTION: "
                f"re-address/clean these rows, or if {agent} should be alive, revive it — CLEARING "
                f"skipped_at re-delivers them to the sweep. (cc-cosem-platform class.)")
            escalations.append({"kind": "dead-foreign", "agent": agent, "ids": newly})

    # STUCK-PAGE pass (Nazim #43063): a row QUIESCED but STILL unread past stuck_page_age_s is
    # genuinely stuck (not normal ~7min latency) → escalate ONCE. skipped_at is taken by the
    # quiesce, so the once-guard is `seen` keyed by ("stuck", row_id) (holds across sweeps for the
    # daemon's life). Only pages a LIVE agent — a dead agent's rows were already dead-foreign-paged.
    stuck_paged: list = []
    stuck_by_agent: dict = {}
    for r in (stuck_rows or []):   # None (a test injected `rows` only) → treated as empty
        ca = _created_at(r)  # UPPER age bound (Nazim #43073): a row older than max_age has already
        if ca is not None:   # paged once in some daemon's life — never re-page it on a restart.
            try:
                if (now_dt - ca).total_seconds() > stuck_page_max_age_s:
                    continue
            except Exception:  # noqa: BLE001 — un-ageable → let the SQL fetch's bound govern
                pass
        if should_backstop_wake(_to_agent(r), _rf(r, 2, "message_type"), _rf(r, 3, "requires_response"),
                                _rf(r, 4, "priority"), _rf(r, 5, "is_test")):
            stuck_by_agent.setdefault(_to_agent(r), []).append(_row_id(r))
    for agent, ids in stuck_by_agent.items():
        new_ids = [i for i in ids if ("stuck", i) not in seen]
        if not new_ids or not _alive(agent):   # already-paged rows, or a dead agent (dead-foreign)
            continue
        if dry_run:
            stuck_paged.append(agent)
            continue
        state = pane_state(agent)
        escalate(
            f"[wake-backstop] {agent} has directed row(s) un-drained past {stuck_page_age_s}s — genuinely stuck (pane: {state})",
            f"TL;DR: row(s) {new_ids} to {agent} are STILL unread {stuck_page_age_s}s+ after they were "
            f"sent — well past normal latency — although {agent} is ALIVE (pane: {state}). They were "
            f"already quiesced (skipped_at) so the sweep isn't re-poking; escalating ONCE now that it's "
            f"genuinely stuck. ACTION: check {agent} — it may be looping/stuck, or the row needs "
            f"re-routing. (read_at is still NULL, so {agent}'s own reconcile can still drain it.)")
        for i in new_ids:
            seen.add(("stuck", i))
        stuck_paged.append(agent)
        escalations.append({"kind": "stuck-page", "agent": agent, "ids": new_ids, "pane": state})

    return {"considered": len(rows), "targets": targets, "woke": woke, "results": results,
            "capped": [_row_id(r) for r in capped], "unreachable": unreachable,
            "dead_foreign": dead_foreign, "live_stuck": live_stuck, "readdress": readdress,
            "vetoed": vetoed, "lookup_failed": lookup_failed, "stuck_paged": stuck_paged,
            "escalations": escalations}


def _ts() -> str:
    """UTC ISO stamp for every log line. Their ABSENCE hid the root-cause of the wake
    storm (Nazim #43073: no per-line time meant no way to see 12 wakes in 13 min)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    dry = "--dry-run" in sys.argv
    once = "--once" in sys.argv
    print(f"{_ts()} wake-backstop-sweep up — cadence={WAKE_SWEEP_SEC}s grace={WAKE_SWEEP_GRACE_S}s "
          f"dry_run={dry} auto_wake_enabled={auto_wake_enabled()}", flush=True)
    while True:
        try:
            # honor the kill-switch: when auto-wake is OFF, observe (dry) — never send.
            eff_dry = dry or not auto_wake_enabled()
            res = sweep_once(dry_run=eff_dry)
            if res["targets"]:
                print(f"{_ts()} sweep: considered={res['considered']} targets={res['targets']} "
                      f"woke={res['woke']} dry={eff_dry}", flush=True)
        except Exception as e:  # fail LOUD to the log, keep the floor alive (KeepAlive re-runs)
            print(f"{_ts()} sweep ERROR: {e!r}", file=sys.stderr, flush=True)
        if once:
            return 0
        time.sleep(WAKE_SWEEP_SEC)


if __name__ == "__main__":
    # nervous_system on sys.path so `import agent_wake` resolves when run directly.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
