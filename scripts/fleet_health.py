#!/usr/bin/env python3
"""fleet_health.py — standing fleet-health sweep (cheap, ~0 model tokens).

The orchestrator used to notice dead lanes only when the operator did. This
runs on a launchd timer so the substrate self-heals:

  1. STALE -> offline: any agent whose heartbeat is older than STALE_MIN is
     marked offline (the Fleet Console then shows reality, not a phantom).
  2. REAP: offline rows older than PRUNE_DAYS are deleted (no 50-day zombies).
  3. RECONCILE: cross-check agent_status against live tmux sessions and flag
     drift — a "working" row with no live session (crashed lane) or a live
     CC session with no fresh row (unregistered lane).
  4. ARCHIVE dead-letters: mark read the unread agent_messages addressed to
     agents that are gone (no fresh heartbeat AND no sent-message in 24h) and
     older than ARCHIVE_MIN_AGE_H. Keeps the console's bus/queue counts honest
     (dead lanes accumulate tombstones nobody drains — operator-flagged 2026-07-16)
     WITHOUT hiding anything live: recipients that heartbeat OR have sent recently
     (incl. unregistered coordinators like cc-orchestrator/orch-console) are
     spared, human-addressed (musa/operator) + substrate rows are never touched,
     and only messages older than the grace window are swept.

Only touches agent_status (status + delete) and agent_messages.read_at (archive).
Run on demand:  python3 scripts/fleet_health.py [--quiet] [--no-archive]
"""
import os, re, subprocess, sys
from pathlib import Path
import psycopg
from dotenv import load_dotenv

# Bootstrap: put the orchestrator root on sys.path so `nervous_system.*` resolves when this is
# run as `python3 scripts/fleet_health.py` (the launchd daemon's invocation), where sys.path[0]
# is scripts/ — NOT the repo root. Without this, _undeliverable()'s lazy nervous_system import
# raised ModuleNotFoundError and crash-looped the job (99de7e3 regression). Same idiom as
# context_health_watchdog.py / priority_sla_watchdog.py / repo_context_watchdog.py.
_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

STALE_MIN = 30      # heartbeat older than this => mark offline
PRUNE_DAYS = 1      # offline rows older than this => delete
ARCHIVE_MIN_AGE_H = 72   # dead-letter grace: only archive unread older than this
SENDER_LIVE_WINDOW_H = 24  # a recipient that SENT within this window is "live" -> spared

# Singletons are NEVER auto-reaped by heartbeat-staleness. Their liveness is
# tracked by lease/reclaim machinery, not agent_status heartbeats — the hub in
# particular self-registers but does NOT continuously heartbeat (boots via
# `claude --continue`, not the launch_dangerous_cc.sh heartbeat loop), so it
# looks "stale" while fully alive, and its status<>'offline' is load-bearing for
# wake resolution (resolve_tmux_session; CAI-RESP-451 / hub #16415). Marking a
# live singleton offline breaks its wake path. The protected set is read ONCE per
# run from the protected_agents table (mig-038 / CAI-762/765) — the SINGLE source
# of truth also consulted by admin_mark_offline + the launcher/watchdog reapers —
# and spares them from offline-marking, pruning, AND the drift check.

# Don't populate prod env from .env under pytest (audit T1: tests stay prod-clean).
if not os.environ.get("PYTEST_CURRENT_TEST"):
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def live_tmux_sessions():
    try:
        out = subprocess.run(["tmux", "ls"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return set()
    return {ln.split(":", 1)[0] for ln in out.splitlines() if ":" in ln}


def observed_activity(recent_bus, tmux_session, live_sessions):
    """OBSERVED-ACTIVITY gate before offline-marking (cc-fleet-health, re-derived from #23952,
    Nazim #24044). A stale HEARTBEAT is not death: an on-demand body (boot_quality.sh has no
    heartbeat loop by design, CAI-729→733) goes stale while working. Before flipping such an
    agent offline, look for OBSERVED activity that proves the identity is alive despite the
    stale column:
      * recent_bus  — it posted a bus row (agent_messages.from_agent) within the staleness
                      window (resolved in SQL against the SAME now() the reaper uses); host-
                      independent and unambiguous.
      * live tmux   — its registered tmux_session is live ON THIS HOST (a co-located pane is up).
    Any observed => (True, reason) -> caller SKIPS the flip. Neither => (False, ...) -> the
    agent is dead as far as we can observe, flip as before. This is 'observation > the heartbeat
    column' (same principle as the pane_busy collapse), NOT a skip-list: a genuinely dead body
    (no hb AND no observed activity) still flips, so this cannot hide a real death — unlike
    adding it to protected_agents, which would (Nazim #24044). cc_session_costs is intentionally
    NOT used: it keys on cc_identity/sub_tag and shared-identity workers mis-map (handoff caveat)."""
    if recent_bus:
        return True, "recent bus row within staleness window"
    if tmux_session and tmux_session in live_sessions:
        return True, f"live tmux session '{tmux_session}' on this host"
    return False, "no observed activity (no recent bus row, no live co-located session)"


def _undeliverable(to_agent) -> bool:
    """A to_agent with NO possible live wake owner — one that can NEVER be woken: the operator
    handle 'musa', the non-address 'substrate', any id that is not a cc-* worker / cai / a
    console. Uses agent_wake's SSOT eligibility on the most-permissive floor (P0 +
    requires_response) so the hub (cc-orchestrator, narrow-floor-eligible) is NEVER misread as
    a dead-letter sink. Distinct from a gone-but-once-live cc lane, which is eligible-by-
    identity and is step 4's job. Imported lazily so importing this module stays
    load_dotenv-free under pytest."""
    if not to_agent:
        return False
    from nervous_system.agent_wake import is_wake_eligible_recipient
    return not is_wake_eligible_recipient(to_agent, "P0", True)


def surface_dead_letters(cur, dry=False):
    """Detect unread rows whose to_agent is structurally undeliverable and SURFACE them to
    orch-console — ONE coalesced row per (to_agent) per day (deduped on today's own surface
    rows), NEVER reaped. Reaping would hide a real misroute; step 4 already spares these.
    Returns the to_agents surfaced (or that WOULD be, when dry)."""
    cur.execute("""SELECT to_agent, count(*) AS n, min(created_at) AS oldest,
                          max(created_at) AS newest,
                          count(*) FILTER (WHERE priority IN ('P0','P1')) AS hi
                   FROM agent_messages
                   WHERE read_at IS NULL AND to_agent IS NOT NULL
                   GROUP BY to_agent""")
    surfaced = []
    for to_agent, n, oldest, newest, hi in cur.fetchall():
        if not _undeliverable(to_agent):
            continue
        # once per (to_agent) per day: skip if I already surfaced this target today.
        cur.execute("""SELECT 1 FROM agent_messages
                       WHERE from_agent='cc-fleet-health' AND to_agent='orch-console'
                         AND subject LIKE %s
                         AND created_at >= date_trunc('day', now()) LIMIT 1""",
                    (f"dead-letter[{to_agent}]:%",))
        if cur.fetchone():
            continue
        if not dry:
            subj = f"dead-letter[{to_agent}]: {n} unread with no live wake owner"
            body = (f"{n} unread agent_messages are addressed to '{to_agent}', which has NO live "
                    f"wake owner — agent_wake will never deliver there, so they accrue silently "
                    f"(oldest {oldest}, newest {newest}, P0/P1={hi}). NOT reaped: a misroute must "
                    f"be fixed (retarget the producer) or the address retired, not hidden. "
                    f"Surfaced once/day by the cc-fleet-health dead-letter detector.")
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute("""INSERT INTO agent_messages
                           (from_agent,to_agent,message_type,subject,body,priority,requires_response)
                           VALUES ('cc-fleet-health','orch-console','update',%s,%s,'P2',false)""",
                        (subj, body))
        surfaced.append(to_agent)
    return surfaced


def main():
    quiet = "--quiet" in sys.argv
    sessions = live_tmux_sessions()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # This sweep runs as cc-fleet-health (the fleet_health lease holder); the
        # reaper below asserts that lease, so declare our own identity here.
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")

        # Protected singletons — ONE source of truth (protected_agents, mig-038):
        # consulted below by offline-marking, prune, AND the drift check.
        cur.execute("SELECT agent_id FROM protected_agents")
        protected = {r[0] for r in cur.fetchall()}

        # STALE -> offline via the sanctioned reaper admin_mark_offline() (mig-037 /
        # CAI-RESP-761): a lease-gated, offline-only, audited SECDEF primitive called
        # once per agent. Replaces the old batch UPDATE, which the hardened
        # enforce_agent_status_identity trigger rejects (one GUC can't match every
        # row). Each call writes a truthful admin_offline_audit row. If our lease has
        # lapsed the call fail-closes and the txn aborts LOUD — the dead-man's switch.
        cur.execute(f"""SELECT agent_id, tmux_session FROM agent_status
                        WHERE status <> 'offline'
                          AND last_heartbeat < now() - interval '{STALE_MIN} minutes'""")
        stale_rows = cur.fetchall()
        marked, kept_alive = [], []
        for _aid, _sess in stale_rows:
            if _aid in protected:
                continue  # singleton — liveness is lease-tracked, never heartbeat-reaped
            # OBSERVED-ACTIVITY gate (re-derived #23952 / Nazim #24044): a stale heartbeat is
            # not death for an on-demand body (boot_quality.sh runs no hb loop by design). If it
            # posted a bus row within the staleness window OR has a live co-located pane, it is
            # alive despite the stale column — DON'T mark it offline (that lie says the fleet's
            # auditor is down mid-audit). Genuinely dead (no hb AND no observed activity) still
            # flips: this is observation-over-heartbeat, NOT a skip-list, so it cannot hide a
            # real death (unlike protected_agents). Window == STALE_MIN, same now() as the reaper.
            cur.execute(f"""SELECT EXISTS(SELECT 1 FROM agent_messages
                            WHERE from_agent = %s
                              AND created_at > now() - interval '{STALE_MIN} minutes')""", (_aid,))
            recent_bus = cur.fetchone()[0]
            observed, why = observed_activity(recent_bus, _sess, sessions)
            if observed:
                kept_alive.append((_aid, why))
                continue
            cur.execute("SELECT admin_mark_offline(%s, %s)",
                        (_aid, f"fleet_health sweep: stale heartbeat > {STALE_MIN}m, no observed activity"))
            if cur.fetchone()[0]:
                marked.append(_aid)

        cur.execute(f"""DELETE FROM agent_status
                        WHERE status='offline'
                          AND last_heartbeat < now() - interval '{PRUNE_DAYS} days'
                          AND agent_id <> ALL(%s)
                        RETURNING agent_id""", (list(protected),))
        pruned = [r[0] for r in cur.fetchall()]
        conn.commit()

        # 4. ARCHIVE dead-letters — mark read unread messages to gone agents.
        # "Gone" = no fresh heartbeat AND hasn't sent in SENDER_LIVE_WINDOW_H
        # (the sent-recently clause spares unregistered coordinators that never
        # write agent_status, e.g. cc-orchestrator/orch-console). Human + substrate
        # rows excluded; only messages past the ARCHIVE_MIN_AGE_H grace are swept.
        archived = []
        if "--no-archive" not in sys.argv:
            cur.execute(f"""
                UPDATE agent_messages SET read_at = now()
                WHERE read_at IS NULL
                  AND to_agent IS NOT NULL
                  AND lower(to_agent) NOT IN ('musa','operator','substrate')
                  AND created_at < now() - interval '{ARCHIVE_MIN_AGE_H} hours'
                  AND to_agent NOT IN (
                      SELECT DISTINCT from_agent FROM agent_messages
                      WHERE from_agent IS NOT NULL
                        AND created_at > now() - interval '{SENDER_LIVE_WINDOW_H} hours')
                  AND to_agent NOT IN (
                      SELECT agent_id FROM agent_status
                      WHERE last_heartbeat > now() - interval '{STALE_MIN} minutes')
                RETURNING to_agent""")
            archived = [r[0] for r in cur.fetchall()]
            conn.commit()

        # 5. SURFACE dead-letters to STRUCTURALLY-UNDELIVERABLE targets (audit #5B). Distinct
        # from step 4 (which reaps gone-but-once-live agents): 'musa'/'substrate' and any non
        # cc-*/cai/console address have NO possible live wake owner, so their unread NEVER
        # drains — step 4 deliberately spares them. Make them VISIBLE (one coalesced row per
        # to_agent per day to orch-console), NEVER reap.
        surfaced = surface_dead_letters(cur)
        if surfaced:
            conn.commit()

        cur.execute("""SELECT agent_id, status, tmux_session,
                       round(extract(epoch from (now()-last_heartbeat))/60) AS m
                       FROM agent_status ORDER BY last_heartbeat DESC NULLS LAST""")
        rows = cur.fetchall()

    # reconcile against ground truth
    drift = []
    registered_sessions = set()
    for aid, status, tmux, m in rows:
        if tmux:
            registered_sessions.add(tmux)
        # Skip singletons: their tmux may live on another host (the hub runs on the
        # VPS, not this Mini) and their liveness is lease/reclaim-tracked, so a local
        # `tmux ls` miss is NOT a crash — flagging it manufactures a false alarm.
        if status == "working" and tmux and tmux not in sessions and aid not in protected:
            drift.append(f"  ! {aid}: 'working' but tmux '{tmux}' is GONE (crashed?)")
    # live CC sessions with no fresh registration (ignore the desktop 'claude' app + helpers)
    for s in sessions:
        if s not in registered_sessions and not re.match(r"^(claude|orchestrator)$", s):
            drift.append(f"  ? tmux '{s}' alive but no agent_status row points to it (unregistered/zombie)")

    # archive is a mutation — always log it (even in --quiet), it's rare + auditable
    if archived:
        from collections import Counter
        by = Counter(archived)
        print(f"archived {len(archived)} dead-letters: " +
              ", ".join(f"{a}×{n}" for a, n in by.most_common()))
    if surfaced:
        print("dead-letter SURFACED to orch-console (no live wake owner): " + ", ".join(surfaced))

    if not quiet:
        if marked:
            print("marked offline (stale heartbeat):", ", ".join(marked))
        if kept_alive:
            print("kept ONLINE despite stale heartbeat (observed activity):",
                  ", ".join(f"{a} ({why})" for a, why in kept_alive))
        if pruned:
            print("reaped (old offline rows):", ", ".join(pruned))
        print(f"live lanes: {sum(1 for r in rows if r[1]=='working')} working / {len(rows)} rows")
        for aid, status, tmux, m in rows:
            print(f"  {aid:<20} {status:<9} hb={m}m  tmux={tmux}")
        if drift:
            print("DRIFT:")
            print("\n".join(drift))
        else:
            print("no drift — DB matches live tmux")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
