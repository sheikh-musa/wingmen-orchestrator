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
import socket
import sys
import time
from datetime import datetime, timezone

import psycopg

# import (not re-encode) the shared policy + wake primitive
from agent_wake import (  # noqa: E402  (same-dir module; nervous_system on sys.path at runtime)
    auto_wake_enabled,
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

# (A) HOST-OWNERSHIP SCOPE (Nazim 37519 — fixes a cross-host false-DEAD, the 451e110 class).
# resolve_tmux_session enumerates only THIS host's panes, but the sweep runs one-instance-per-
# host against the SHARED substrate DB with no host filter. So a live CROSS-HOST agent resolves
# to "no live session" on the wrong host — and (A) would declare it DEAD, quiesce (skipped_at,
# which then defeats the OWNING host's sweep too via the shared DB) + false-escalate. The (B)
# age-cap is host-agnostic + benign (escalates to a human, leaves read_at NULL for the target's
# own reconcile), so ONLY (A) needs the host scope. Fix: an instance may (A)-declare-dead only
# an agent it is the wake-OWNER of (homed here, whose pane a local resolve can see). A local miss
# for a NON-owned agent means "not mine — leave the row for the owning instance", never "dead".
HUB_AGENT = "cc-orchestrator"          # the one cross-host body today (homed on the orch_lease host)
_MINI_HOSTS = {h.strip() for h in (os.environ.get("WAKE_SWEEP_MINI_HOSTS") or "Sheikhs-Mini").split(",") if h.strip()}


def _default_owns_agent(agent: str) -> bool:
    """Does THIS sweep instance own `agent` for (A) dead-inference? Explicit
    WAKE_SWEEP_OWNED_AGENTS (comma-list) wins — the durable per-instance enforce-in-code answer
    for multi-host (set it on each host as the fleet spreads to the Gazzabyte VPS). Absent it,
    a safe host-derived default: the Mini owns every lane EXCEPT the cross-host hub; any other/
    unknown host owns NOTHING for (A) until explicitly configured (fail-safe — an unconfigured
    foreign instance never false-DEADs a cross-host body, it just doesn't run (A))."""
    env = os.environ.get("WAKE_SWEEP_OWNED_AGENTS")
    if env is not None:
        owned = {a.strip() for a in env.split(",") if a.strip()}
        return agent in owned
    host = os.environ.get("WAKE_SWEEP_HOST") or socket.gethostname()
    if host in _MINI_HOSTS:
        return agent != HUB_AGENT
    return False

# ── DEPLOY NOTE (Nazim 37524) — when the VPS-for-hub sweep instance is stood up ──────────
# 1. Set WAKE_SWEEP_OWNED_AGENTS=cc-orchestrator on that instance (it owns ONLY the hub; a
#    bare/unconfigured foreign instance owns nothing for (A), so it fails safe until set).
# 2. In the SAME change, add the orch_lease belt to (A): on the VPS owns(cc-orchestrator)=True,
#    so a transient hub tmux-restart blip that exceeds the grace while the orch_lease is STILL
#    FRESH could (A)-dead a briefly-alive hub on its OWN host. Guard it — never (A)-declare the
#    hub dead while singleton_liveness.hub_lease_fresh() is True (a 3-line reuse of the 451e110
#    cross-host-liveness signal). Redundant for every OTHER case (ownership scoping subsumes it),
#    so it lands WITH the VPS instance, not before — captured here so it isn't a promise-to-remember.
# The durable multi-host answer (beyond env allowlists) is a `host` column on the lane/agent
# registry so ownership is enforced-in-code from the registry, not per-instance env.

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


def _mark_skipped(row_ids) -> None:
    """Set skipped_at on the given rows — quiesces them (the SQL excludes skipped_at IS
    NOT NULL) AND is the once-guard (a skipped row is never re-fetched → never re-
    escalated). As cc-fleet-health (identity set for the row trigger). No-op on empty."""
    ids = [i for i in (row_ids or []) if i is not None]
    if not ids or not _DSN:
        return
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute("UPDATE agent_messages SET skipped_at=now() "
                    "WHERE id = ANY(%s) AND skipped_at IS NULL", (ids,))
        conn.commit()


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
               mark=_mark_skipped, escalate=_escalate_operator, owns=_default_owns_agent,
               cap_age_s: int = WAKE_SWEEP_CAP_AGE_S, now: float | None = None,
               now_dt=None, dry_run: bool = False) -> dict:
    """One pass WITH BACKOFF (Nazim 37512). Find rotting directed rows; wake each eligible
    recipient of a FRESH (under-cap) row once; then give up on two classes so a stuck row
    escalates ONCE instead of poking forever:
      (A) a target that resolves to NO live session (dead/unreachable) → escalate-once +
          quiesce ALL its rotting rows. Reachability is the PANE signal (computed by
          wake_agent), never a heartbeat/status field — so a wakeable on-demand body is
          never false-rotted. Kills the cc-cosem-platform class.
      (B) a row unread past cap_age_s → escalate-once + quiesce it. Handles the live-but-
          stuck class (the cai 60+-empty-wakes case).
    skipped_at is the once-guard (excludes the row from every future sweep). Injectable
    rows/wake/mark/escalate make it unit-testable without DB or tmux; dry_run mutates
    nothing (observe-only, honoring the AUTO_WAKE_ENABLED kill-switch)."""
    if rows is None:
        rows = _fetch_rows(grace_s)
    now_dt = now_dt if now_dt is not None else datetime.now(timezone.utc)

    # (B) partition FIRST: a capped row is pulled out before waking, so it never drives a
    # wake and is handled exactly once (the capped path) — no double-processing.
    capped = [r for r in rows if is_capped(r, now_dt, cap_age_s)]
    fresh = [r for r in rows if not is_capped(r, now_dt, cap_age_s)]

    targets = eligible_recipients(fresh)
    results = {a: wake(a, reason="backstop-sweep", dry_run=dry_run, now=now) for a in targets}
    woke = [a for a, r in results.items() if isinstance(r, dict) and r.get("woke")]

    # (A) targets that came back with NO live session → dead/unreachable
    unreachable = sorted(
        a for a, r in results.items()
        if isinstance(r, dict) and r.get("why") == "no live session")

    # (A) HOST SCOPE: only DECLARE-DEAD an agent this instance OWNS. A local-pane miss for a
    # cross-host (non-owned) agent is "not mine, leave it for the owning instance", NOT dead —
    # this is what prevents the 451e110 false-DEAD (e.g. the Mini seeing the VPS hub as "dead").
    dead_owned = [a for a in unreachable if owns(a)]
    left_foreign = [a for a in unreachable if not owns(a)]

    escalations: list[dict] = []
    if not dry_run:
        for agent in dead_owned:  # (A) one page + quiesce per dead OWNED agent
            ids = [_row_id(r) for r in fresh if _to_agent(r) == agent]
            mark(ids)
            escalate(
                f"[wake-backstop] directed rows rotting to dead/unreachable agent {agent}",
                f"TL;DR: {len(ids)} unread directed row(s) to {agent} can't be delivered — it "
                f"resolves to NO live session (dead/absent pane), so re-poking it forever is "
                f"pointless. Quiesced (skipped_at) + escalated ONCE. ACTION: re-address or clean "
                f"these rows (ids={ids}); they stay unread in {agent}'s inbox if it returns. "
                f"(cc-cosem-platform class.)")
            escalations.append({"kind": "dead-agent", "agent": agent, "ids": ids})
        for r in capped:  # (B) one page + quiesce per stuck ROW
            rid, ta = _row_id(r), _to_agent(r)
            mark([rid])
            escalate(
                f"[wake-backstop] row {rid} to {ta} un-drained past the re-wake cap — needs a human",
                f"TL;DR: directed row {rid} to {ta} stayed unread past the re-wake cap "
                f"(~{cap_age_s}s, ~{WAKE_SWEEP_CAP_N} pokes). The sweep STOPPED poking + quiesced it "
                f"(skipped_at) + escalated ONCE. It is still read_at IS NULL in {ta}'s OWN inbox, so "
                f"{ta}'s normal reconcile still drains it — quiescing only stops the sweep's redundant "
                f"re-wakes. ACTION: nudge {ta}, or route to lane_wedge if it's genuinely stuck.")
            escalations.append({"kind": "capped", "id": rid, "agent": ta})

    return {"considered": len(rows), "targets": targets, "woke": woke, "results": results,
            "capped": [_row_id(r) for r in capped], "unreachable": unreachable,
            "dead_owned": dead_owned, "left_foreign": left_foreign,
            "escalations": escalations}


def main() -> int:
    dry = "--dry-run" in sys.argv
    once = "--once" in sys.argv
    print(f"wake-backstop-sweep up — cadence={WAKE_SWEEP_SEC}s grace={WAKE_SWEEP_GRACE_S}s "
          f"dry_run={dry} auto_wake_enabled={auto_wake_enabled()}", flush=True)
    while True:
        try:
            # honor the kill-switch: when auto-wake is OFF, observe (dry) — never send.
            eff_dry = dry or not auto_wake_enabled()
            res = sweep_once(dry_run=eff_dry)
            if res["targets"]:
                print(f"sweep: considered={res['considered']} targets={res['targets']} "
                      f"woke={res['woke']} dry={eff_dry}", flush=True)
        except Exception as e:  # fail LOUD to the log, keep the floor alive (KeepAlive re-runs)
            print(f"sweep ERROR: {e!r}", file=sys.stderr, flush=True)
        if once:
            return 0
        time.sleep(WAKE_SWEEP_SEC)


if __name__ == "__main__":
    # nervous_system on sys.path so `import agent_wake` resolves when run directly.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
