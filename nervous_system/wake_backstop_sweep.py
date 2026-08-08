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
import sys
import time

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

# Broader-than-realtime row predicate. NULL-safe: a NULL is_test counts as not-test,
# a NULL priority counts as not-P3 (still swept). read_at/​skipped_at gate the quiesce.
_SWEEP_SQL = """
    SELECT id, to_agent, message_type, requires_response, priority, is_test
    FROM agent_messages
    WHERE read_at IS NULL
      AND skipped_at IS NULL
      AND is_test IS NOT TRUE
      AND priority IS DISTINCT FROM 'P3'
      AND created_at < now() - make_interval(secs => %s)
    ORDER BY to_agent, created_at
"""


def eligible_recipients(rows) -> list[str]:
    """Pure: the deduped, order-stable set of recipients to wake. The SQL is a coarse
    prefilter; the AUTHORITATIVE per-row decision is agent_wake.should_backstop_wake
    (canonical policy, never forked into SQL — cc-quality #16848), applied here as
    defense-in-depth (a row is dropped if it fails the canonical gate even if the SQL
    let it through). Rows are tuples (id, to_agent, message_type, requires_response,
    priority, is_test) per _SWEEP_SQL, or mappings with those keys."""
    out: list[str] = []
    for r in rows:
        if isinstance(r, dict):
            to_agent, mt, rr, prio, istest = (
                r["to_agent"], r.get("message_type"), r.get("requires_response"),
                r.get("priority"), r.get("is_test"))
        else:
            _id, to_agent, mt, rr, prio, istest = r
        if should_backstop_wake(to_agent, mt, rr, prio, istest) and to_agent not in out:
            out.append(to_agent)
    return out


def _fetch_rows(grace_s: int):
    if not _DSN:
        raise RuntimeError("wake_backstop_sweep: no DATABASE_URL/SUPABASE_DB_URL")
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute(_SWEEP_SQL, (grace_s,))
        return cur.fetchall()


def sweep_once(*, grace_s: int = WAKE_SWEEP_GRACE_S, rows=None, wake=wake_agent,
               dry_run: bool = False, now: float | None = None) -> dict:
    """One pass: find rotting directed rows, wake each eligible recipient once.
    Injectable `rows` and `wake` make the selection logic unit-testable without DB
    or tmux. Idempotency/debounce/cap/busy all live in `wake` (agent_wake.wake_agent)."""
    if rows is None:
        rows = _fetch_rows(grace_s)
    targets = eligible_recipients(rows)
    results = {a: wake(a, reason="backstop-sweep", dry_run=dry_run, now=now) for a in targets}
    woke = [a for a, r in results.items() if isinstance(r, dict) and r.get("woke")]
    return {"considered": len(rows), "targets": targets, "woke": woke, "results": results}


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
