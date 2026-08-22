#!/usr/bin/env python3
"""auto_recycle_on_bloat.py — STAGE 0 (DETECT-ONLY): the durable answer to op#18444
("bloated agents not recycled"). An INDEPENDENT trigger (sre-liveness-watchdog pattern)
that will — once armed through the observe-first ladder — recycle a WORKER LANE that
crosses the RED FIRE threshold, autonomously, no human flagging.

STAGE 0 FIRES NOTHING. It reads the same context gauge as context_health_watchdog,
evaluates the safety gates, and LOGS the decision it WOULD make. Its whole job right now
is to PROVE the gates — especially gate-2 (not-self-compacted) — over several days before
Stage-1 (supervised) is earned. Design + this file blessed by orch-console (bus 18526);
build follows the ladder, nothing auto-fires until console signs each stage.

SAFETY GATES (all must hold to fire; each learned from the 2026-08-11 manual sweep):
  G4 TIER (CODE-ENFORCED, FAIL-CLOSED): only a POSITIVELY-resolved worker lane is
     eligible. Singletons (cai/hub/console/SRE) + any UNKNOWN/unresolvable body are
     treated as singleton => detect+escalate, NEVER auto-fire. Enforced via
     scripts/lib/fleet_health_boundaries.py so the recycler is STRUCTURALLY unable to
     fire a singleton/self reset, not merely polite about it.
  G2 NOT-SELF-COMPACTED (THE load-bearing gate): bodies at ~100% frequently AUTO-COMPACT
     mid-turn and self-recover to green while working (cc-irsyad-1 AND -2 both did today,
     100%->35%). So a FRESH pct re-read is the LAST step before any fire; if it dropped
     below threshold, SKIP. reset_lane's busy-refusal does NOT catch a lane that
     self-compacted and went idle, so G2 is the sole guard for that case.
  G1 CLEAN-IDLE (not a flicker), G3 ghost/staged-safe (inherited from the shipped
     CC_GHOST fix + reset preserve), G5 re-derive/checkpoint boot, G6 debounce/anti-loop,
     G7 dead-man fail-loud — see reports/auto-recycle-on-bloat-design.md.

THRESHOLDS: FIRE at 88% (DISTINCT from context_health's ~80% ALERT-red — auto-recycle
acts at a higher bar than alerting, to avoid over-recycling). A ~95% HARD line shortens
G1's idle-patience only — it NEVER bypasses G2 and NEVER fires a busy body.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from dataclasses import dataclass

from scripts import context_health_watchdog as chw
from scripts.lib import fleet_health_boundaries as bnd
from scripts.lib import pane_bloat_signal as pbs

FIRE_THRESHOLD = 88      # legacy GAUGE fire bar (% of 1M) — see PANE_FIRE_K below
HARD_LINE = 95           # shortens G1 idle-patience only; never touches G2 / never fires busy

# op#13050 (2026-08-15): the DB gauge is BLIND to bloated-but-idle worker lanes (stale
# on idle) and MIS-MAPS sub-tag instances onto their base row (verified: cc-irsyad gauge
# 8% while its pane showed 795.9k). So detection moves to the LIVE PANE signal
# (scripts/lib/pane_bloat_signal) — CC's own `/clear to save {N}k tokens` reclaim hint.
# PANE_FIRE_K = the IDLE-recycle fire bar in K-tokens. LOWERED ~85%(850) -> ~65%(650)
# per Musa op#15956 / Nazim #31751: "if it has to reach 94% it's broken." Idle-recycle
# is FREE (no work lost), so recycle an IDLE lane BEFORE the red zone instead of near the
# cliff (coord riding 94->98% while active was the proof it was set too high). The idle
# gate still applies — a BUSY/STAGED lane at >=650k is GATED, never fired (never-interrupt
# stays; only the IDLE bar dropped). Value CONFIRMED by Nazim #31754 (#31751 said ~65%).
PANE_FIRE_K = 650
# WATCH_K = the OBSERVE-window verbose bar. Lanes at/above this (but maybe below the fire
# bar) get their pane signal + idle verdict logged every cycle, so console's Stage-0
# review sees the VARIED states (busy/staged/sub-tag/idle) being correctly NOT-fired and
# can watch a lane climb toward the bar. Quiet lanes below it are only counted.
WATCH_K = 500

# ── STAGE-1 WARN (op#13308, nazim-SIGNED bus 22320) — NON-DESTRUCTIVE ─────────────
# Fires NO reset. When a worker lane crosses the pane-truth fire bar at a CLEAN IDLE
# (a WOULD-FIRE verdict), page the console so a human/SRE recycles it BEFORE it hits
# 100% — the operator stops being the catcher (fleet catches at ~85%). GATED behind
# AUTO_RECYCLE_STAGE1_WARN=1 (default OFF: this file deploys INERT; the warn only goes
# live once the env flag is set on the daemon). Deduped per-lane on a cooldown so the
# 5-min daemon never re-pages the same lane. Fail-safe: any error logs + skips — a warn
# blip must NEVER crash the observe pass or fire anything. Stage-2 (auto-fire) stays
# separately gated (audit-commit + self-checkpoint + coverage — nazim re-review).
STAGE1_WARN_ENABLED = os.environ.get("AUTO_RECYCLE_STAGE1_WARN") == "1"
STAGE1_WARN_DRY = os.environ.get("AUTO_RECYCLE_WARN_DRY") == "1"   # print instead of insert (test)
WARN_COOLDOWN_MIN = 90
# op#13308/#13323 (nazim bus 22343): page cc-fleet-health PRIMARY so the SRE closes the
# recycle loop (self-checkpoint-first → recycle) WITHOUT routing through the operator/Nazim
# — that IS the mission. orch-console is CC'd for visibility (non-blocking). Stage-1.5:
# human-in-loop = the SRE, not the operator, until Stage-2 removes the human.
WARN_RECIPIENTS = ("cc-fleet-health", "orch-console")


# ── G4: tier eligibility — PURE + fail-closed (unit-tested) ──────────────────
def _base_agent(agent_id: str) -> str:
    """Strip a trailing -N sub-tag: cc-irsyad-1 -> cc-irsyad, cc-storefront-b-1 ->
    cc-storefront-b. Keeps a lettered sub-family (cc-storefront-b) — only a numeric
    tail is a sub-tag."""
    parts = agent_id.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return agent_id


def tier_eligible(agent_id: str, singletons: "frozenset[str] | set[str]",
                  worker_bases: "frozenset[str] | set[str]") -> bool:
    """G4, FAIL-CLOSED. Eligible for auto-fire ONLY if the body is a POSITIVELY-resolved
    worker lane: its base is in `worker_bases` (authoritative, e.g. fleet_lanes) AND NOT a
    singleton. Anything else — a singleton, or a base the classifier does not know — is
    NOT eligible (treated as singleton: detect+escalate, never fire). A new lane the
    classifier hasn't learned yet defaults to no-fire, by construction."""
    base = _base_agent(agent_id)
    if base in singletons or agent_id in singletons:
        return False                      # explicit singleton
    if base not in worker_bases:
        return False                      # UNKNOWN/unresolvable -> fail-closed -> no-fire
    return True


# ── G2: not-self-compacted — PURE (unit-tested) ─────────────────────────────
def self_compacted(fresh_pct: "int | None", threshold: int = FIRE_THRESHOLD) -> bool:
    """G2. True if a FRESH context% read has dropped BELOW the fire threshold — the body
    auto-compacted and self-recovered, so we must SKIP (never recycle a self-healed lane).
    A None/unknown fresh read fails CLOSED -> treated as self-compacted (skip): if we can't
    confirm it's still bloated at fire-time, we do not fire."""
    if fresh_pct is None:
        return True
    return fresh_pct < threshold


# ── Stage-0 decision (fires NOTHING; classifies + logs) ─────────────────────
@dataclass
class Decision:
    agent: str
    pct: int
    verdict: str        # WOULD-FIRE | GATED | SINGLETON-ESCALATE | SELF-COMPACTED
    reason: str


def classify(a: "chw.AgentCtx", singletons, worker_bases, fresh_pct: "int | None",
             idle: "bool | None") -> Decision:
    """Pure Stage-0 classification of ONE red body. Order matters: G4 (tier) is
    structural + first; then G2 (fresh self-compact) as the load-bearing skip; then G1
    (clean-idle). WOULD-FIRE is emitted only when a worker lane is still-bloated + idle."""
    if a.stale:
        return Decision(a.agent, a.pct, "GATED", f"stale telemetry ({a.age_s}s) — never act on a stale reading")
    if not tier_eligible(a.agent, singletons, worker_bases):
        return Decision(a.agent, a.pct, "SINGLETON-ESCALATE",
                        "not a resolved worker lane (singleton/unknown) — detect+escalate, NEVER auto-fire (G4 fail-closed)")
    # G2 — THE load-bearing gate, on a FRESH read (the caller re-read right before this).
    if self_compacted(fresh_pct):
        return Decision(a.agent, a.pct, "SELF-COMPACTED",
                        f"was {a.pct}% at detect, FRESH read {fresh_pct}% < {FIRE_THRESHOLD}% — auto-compacted+self-recovered, SKIP (G2 save)")
    if idle is not True:
        return Decision(a.agent, a.pct, "GATED", "not at a confirmed clean idle — wait (G1); never fire a busy lane")
    return Decision(a.agent, a.pct, "WOULD-FIRE",
                    f"worker lane, fresh {fresh_pct}% >= {FIRE_THRESHOLD}%, clean idle — Stage-2 would recycle here")


def _worker_bases_from_fleet_lanes() -> "set[str]":
    """Authoritative worker-lane bases from fleet_lanes (minus singleton bodies). DB read;
    on any failure returns an EMPTY set -> tier_eligible fails closed for everyone (no-fire)."""
    try:
        import os, psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT base_agent_id FROM fleet_lanes WHERE base_agent_id IS NOT NULL")
            bases = {r[0] for r in cur.fetchall()}
        return bases - set(bnd.SINGLETON_BODIES)
    except Exception as e:  # noqa: BLE001 — fail-closed
        print(f"[auto-recycle] WARN worker-base resolve failed ({e}) — fail-closed, no lane eligible", file=sys.stderr)
        return set()


def _fresh_pct(agent: str) -> "int | None":
    """G2's FRESH re-read — the freshest gauge value for one body, read AGAIN right before
    the (hypothetical) fire, never cached. Returns None (=> fail-closed skip) on any doubt."""
    try:
        for a in chw.read_context_gauge():
            if a.agent == agent:
                return None if a.stale else a.pct
    except Exception:  # noqa: BLE001
        return None
    return None


# ── PANE-BASED observe pass (op#13050-A) — enumerate LIVE worker lanes ───────
def _live_worker_sessions(tmux: str = pbs.DEFAULT_TMUX) -> "list[tuple[str, str | None]]":
    """Enumerate LIVE tmux sessions and map each to its fleet_lanes base_agent_id.
    Returns [(session, base_or_None)]. A session absent from fleet_lanes maps to None
    (unknown -> fails closed at the tier gate). On any tmux/DB error returns [] so the
    pass no-ops rather than acting on a partial world (fail-closed)."""
    try:
        r = subprocess.run([tmux, "ls", "-F", "#{session_name}"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return []
        sessions = [s for s in r.stdout.split() if s]
    except Exception as e:  # noqa: BLE001
        print(f"[auto-recycle] WARN tmux enumerate failed ({e}) — fail-closed, no lane", file=sys.stderr)
        return []
    lane2base: "dict[str, str]" = {}
    try:
        import os, psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            cur.execute("SELECT lane, base_agent_id FROM fleet_lanes")
            lane2base = {lane: base for lane, base in cur.fetchall()}
    except Exception as e:  # noqa: BLE001 — no map => every session base=None => fail-closed
        print(f"[auto-recycle] WARN fleet_lanes map failed ({e}) — sessions unresolved", file=sys.stderr)
    return [(s, lane2base.get(s)) for s in sessions]


def _idle_verdict(session: str, base: "str | None") -> str:
    """body_activity_oracle verdict for a LIVE local session, forced onto THIS session
    (not the agent_id resolver — we key on the tmux session). Any failure -> UNSURE."""
    try:
        from nervous_system import body_activity_oracle as oracle
        v = oracle.activity(base or session,
                            resolve_session=lambda _a: session,
                            resolve_host=lambda _a: oracle.LOCAL_HOST)
        return v.state
    except Exception as e:  # noqa: BLE001 — never guess
        return f"UNSURE:{type(e).__name__}"


def classify_pane(session: str, base: "str | None", singletons, worker_bases,
                  bloat_k: "float | None", verdict_state: str) -> Decision:
    """PURE Stage-0 classification of ONE live worker session from the LIVE PANE signal.
    Order: bloat (fresh pane, fail-closed on None) -> G4 tier (structural) -> G1 clean-
    idle (pane-truth oracle). WOULD-FIRE only for a resolved worker lane that is still
    bloated AND at a clean IDLE_EMPTY composer. Fires nothing — Stage-0."""
    kdisp = f"{bloat_k:.0f}k" if bloat_k is not None else "no-hint"
    kint = int(bloat_k or 0)
    if not pbs.is_bloated_k(bloat_k, PANE_FIRE_K):
        return Decision(base or session, kint, "NOT-BLOATED",
                        f"pane {kdisp} < {PANE_FIRE_K}k fire bar (or mid-turn/no hint) — nothing to do")
    if base is None or not tier_eligible(base, singletons, worker_bases):
        return Decision(base or session, kint, "SINGLETON-ESCALATE",
                        f"pane {kdisp} >= fire bar but NOT a resolved worker lane (singleton/unknown '{base or session}') — detect+escalate, NEVER auto-fire (G4 fail-closed)")
    if verdict_state == "IDLE_EMPTY":
        return Decision(base, kint, "WOULD-FIRE",
                        f"worker lane, pane {kdisp} >= {PANE_FIRE_K}k, oracle IDLE_EMPTY — Stage-2 would checkpoint->reset here")
    return Decision(base, kint, "GATED",
                    f"pane {kdisp} >= fire bar but oracle={verdict_state} (not clean-idle) — wait; never fire a busy/staged/unsure lane (G1)")


# ── PANE-TRUTH PUBLISHER (op#13050-B) — Mini panes -> pane_context (console reads) ──
_PRUNE_STALE_H = 1  # dead-session rows age out after this many hours (console ignores via TTL anyway)


def publish_pane_context() -> int:
    """Publish fresh pane-truth for EVERY live Mini-local session into `pane_context`
    (op#13050-B, console-signed 21515). One UPSERT per live tmux session: (session, base,
    pane_k, pct, idle_verdict, host, updated_at=now()); pane_k is NULL when no /clear hint
    shows (low ctx OR mid-turn — idle_verdict disambiguates). pct (op#13186) is CC's
    `{N}% context used` line — the CLIFF truth present exactly when pane_k vanishes at
    >=~95%, so a maxed lane still publishes its real fill instead of a NULL. READ-ONLY of the panes;
    writes only a cache row. The VPS console reads FRESH rows (TTL) for its honest header;
    a stale/absent row => UNKNOWN (NEVER a gauge fallback — the gauge is the bug). The VPS
    hub is NOT a Mini tmux session so it is correctly absent here (self-publishes as a
    fast-follow). FAIL-SAFE: any error is logged + skipped so a publish blip degrades the
    console to UNKNOWN, never crashes the observe pass or fires anything."""
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = None
    sessions = _live_worker_sessions()   # (session, base) for ALL live tmux sessions
    rows = []
    for session, base in sessions:
        try:
            # ONE capture, BOTH signals (op#13186): pct is CC's `{N}% context used` line
            # (authoritative at/near the cliff), pane_k is the `/clear to save {N}k` hint
            # (present below the cliff). At >=~95% the hint vanishes so pane_k is NULL but
            # pct carries the truth; below it pct is NULL and pane_k carries it. Both may
            # be NULL (low ctx OR mid-turn) — fail-safe UNKNOWN, never a fake 0.
            pane_text = pbs.capture_pane(session)
            k = pbs.parse_hint_k(pane_text)
            pct = pbs.parse_pct(pane_text)
            verdict = _idle_verdict(session, base)
        except Exception as e:  # noqa: BLE001 — a capture blip must not drop the whole publish
            print(f"[pane-context] WARN read failed for {session} ({e}) — skip", file=sys.stderr)
            continue
        rows.append((session, base, k, pct, verdict, host))
    if not rows:
        return 0
    try:
        import os, psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            cur.executemany(
                """INSERT INTO pane_context (session, base, pane_k, pct, idle_verdict, host, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (session) DO UPDATE
                     SET base=EXCLUDED.base, pane_k=EXCLUDED.pane_k, pct=EXCLUDED.pct,
                         idle_verdict=EXCLUDED.idle_verdict, host=EXCLUDED.host, updated_at=now()""",
                rows)
            cur.execute("DELETE FROM pane_context WHERE updated_at < now() - make_interval(hours => %s)",
                        (_PRUNE_STALE_H,))
        print(f"[pane-context] published {len(rows)} live session(s)")
        return len(rows)
    except Exception as e:  # noqa: BLE001 — publish is best-effort; console fails safe to UNKNOWN
        print(f"[pane-context] WARN publish failed ({e}) — console shows UNKNOWN (fail-safe)", file=sys.stderr)
        return 0


def run_observe_panes() -> int:
    """DETECT-ONLY sweep over LIVE worker lanes via the PANE signal (op#13050-A).
    Fires NOTHING. Reads the fresh pane bloat signal for every live session; for any lane
    at/above WATCH_K it also reads the pane-truth idle verdict and classifies, LOGGING the
    decision it WOULD make with the pane signal + idle verdict inline (Stage-0 observe
    record for console's review — proves zero false WOULD-FIRE across the varied states).
    This is the advancement over the gauge pass: a bloated-but-idle worker whose DB gauge
    is stale/mis-mapped is now VISIBLE (its pane never lies), and it fires NOTHING."""
    singletons = frozenset(bnd.SINGLETON_BODIES)
    worker_bases = _worker_bases_from_fleet_lanes()
    sessions = _live_worker_sessions()
    watched = []   # (session, base, k, verdict_state, Decision) for lanes >= WATCH_K
    quiet = 0      # lanes below WATCH_K (no hint / low context) — counted only
    for session, base in sessions:
        try:
            k = pbs.pane_bloat_k(session)
        except Exception as e:  # noqa: BLE001 — a capture blip must not crash the sweep
            print(f"[auto-recycle] WARN pane read failed for {session} ({e}) — skip", file=sys.stderr)
            continue
        if k is None or k < WATCH_K:
            quiet += 1
            continue
        verdict = _idle_verdict(session, base)
        d = classify_pane(session, base, singletons, worker_bases, k, verdict)
        watched.append((session, base, k, verdict, d))
    fires = [w for w in watched if w[4].verdict == "WOULD-FIRE"]
    print(f"[auto-recycle STAGE-0 PANE detect-only] {len(sessions)} live session(s) "
          f"({quiet} quiet <{WATCH_K}k); {len(watched)} watched >={WATCH_K}k; "
          f"{len(fires)} WOULD-FIRE; FIRING NOTHING")
    for session, base, k, verdict, d in sorted(watched, key=lambda w: -w[2]):
        tag = d.verdict if d.verdict != "NOT-BLOATED" else "WATCH"
        print(f"  [{tag}] {d.agent} (session={session} pane={k:.0f}k idle={verdict}): {d.reason}")
    warn_console(fires)   # Stage-1 (op#13308): NON-destructive deduped console page on WOULD-FIRE
    escalates = select_escalates(watched)   # op#28437 (Musa's invisibility gap): unresolved bloated-idle lanes
    if escalates:
        print(f"[auto-recycle STAGE-0 PANE] {len(escalates)} SINGLETON-ESCALATE lane(s) "
              f"idle >= fire bar but NOT a resolved worker lane — surfacing (warn_escalate); FIRING NOTHING")
    warn_escalate(escalates)   # Stage-1 sibling: DISTINCT deduped WARN for the invisible-to-warn_console lanes
    return 0


def select_escalates(watched):
    """PURE (unit-tested). From the observe-pass `watched` tuples
    (session, base, k, verdict_state, Decision), pick the lanes that are BOTH
    SINGLETON-ESCALATE (op#28437 / Musa's invisibility gap: pane >= PANE_FIRE_K but NOT a
    resolved worker lane — a singleton, or a worker MISSING from fleet_lanes; G4 fail-closed)
    AND at a clean idle (verdict_state == 'IDLE_EMPTY'). These lanes are NEVER a WOULD-FIRE,
    so warn_console never surfaces them and they climb to 100% unseen. Idle is required (never
    nag a working/staged/unsure body); WOULD-FIRE is excluded by construction (warn_console
    owns those — no double-page). Fires NOTHING — pure selection."""
    return [w for w in watched if w[4].verdict == "SINGLETON-ESCALATE" and w[3] == "IDLE_EMPTY"]


def warn_escalate(escalates) -> int:
    """STAGE-1 (op#28437, Musa's invisibility gap — sibling of warn_console). NON-DESTRUCTIVE.
    For each SINGLETON-ESCALATE lane at a clean idle (pane >= PANE_FIRE_K but NOT a resolved
    worker lane), post a DEDUPED WARN so the SRE either REGISTERS it in fleet_lanes (if it is a
    worker lane the classifier hasn't learned) or manually recycles it (if a genuine singleton)
    BEFORE it hits 100%. These lanes never reach warn_console (they are not WOULD-FIRE), so
    without this they are invisible at the detection layer — the exact gap Musa flagged.
    Returns count paged. No-op unless AUTO_RECYCLE_STAGE1_WARN=1 (deploys INERT, same as
    warn_console). DISTINCT subject prefix ('WARN unresolved bloat:') so the per-lane dedup key
    never collides with warn_console's 'WARN worker bloat:'. Same gate/dedup/cooldown/fail-safe
    shape as warn_console: any error logs + skips — a warn blip must NEVER crash the observe
    pass or fire anything."""
    if not STAGE1_WARN_ENABLED or not escalates:
        return 0
    sent = 0
    try:
        import psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            for session, base, k, verdict, d in escalates:
                agent = d.agent
                subject = f"WARN unresolved bloat: {agent} ~{k:.0f}k idle — register in fleet_lanes or manually recycle"
                for to_agent in WARN_RECIPIENTS:
                    cur.execute(
                        "SELECT 1 FROM agent_messages WHERE from_agent=%s AND to_agent=%s "
                        "AND subject LIKE %s AND created_at > now() - make_interval(mins => %s) LIMIT 1",
                        (bnd.SRE_AGENT_ID, to_agent, f"WARN unresolved bloat: {agent} %", WARN_COOLDOWN_MIN))
                    if cur.fetchone():
                        continue   # this recipient already warned for this lane within cooldown
                    if to_agent == "cc-fleet-health":
                        body = (
                            f"ACT (Stage-1, op#28437 / Musa's invisibility gap): {agent} (session {session}) is at "
                            f"~{k:.0f}k pane-truth (>= {PANE_FIRE_K}k, ~65% of 1M) and CLEAN IDLE, but it is NOT a "
                            f"resolved worker lane — a singleton, or a worker MISSING from fleet_lanes (G4 fail-closed "
                            f"=> SINGLETON-ESCALATE, never a WOULD-FIRE, so warn_console never sees it). Close the loop "
                            f"at SRE: (1) if it's a WORKER lane missing from fleet_lanes, REGISTER it (base, worktree, "
                            f"launcher) so it resolves as a worker and normal Stage-1/2 covers it; (2) if it's a genuine "
                            f"SINGLETON, self-checkpoint-first + checkpoint-first recycle via scripts/sre_lane_recycle.py; "
                            f"(3) CC orch-console. NEVER blind-recycle a body with a pending plan (op#13323). "
                            f"Deduped {WARN_COOLDOWN_MIN}m/lane.")
                    else:
                        body = (
                            f"FYI (non-blocking): {agent} (session {session}) ~{k:.0f}k idle >= fire bar but UNRESOLVED "
                            f"(singleton, or not in fleet_lanes) — invisible to the normal worker-bloat WARN. "
                            f"cc-fleet-health (SRE) is paged to register-or-recycle it before 100%. Non-destructive "
                            f"Stage-1 WARN; loop closes at SRE, not you/the operator.")
                    if STAGE1_WARN_DRY:
                        print(f"[auto-recycle STAGE-1 ESCALATE-WARN DRY] would page {to_agent} :: {subject}")
                    else:
                        cur.execute(
                            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
                            "VALUES (%s,%s,'update',%s,%s,false,'P2')",
                            (bnd.SRE_AGENT_ID, to_agent, subject, body))
                    sent += 1
            if not STAGE1_WARN_DRY:
                c.commit()
        if sent:
            print(f"[auto-recycle STAGE-1 ESCALATE-WARN{' DRY' if STAGE1_WARN_DRY else ''}] "
                  f"paged for {sent} unresolved bloated-idle lane(s)")
    except Exception as e:  # noqa: BLE001 — warn is best-effort; never crash the pass / fire anything
        print(f"[auto-recycle] WARN stage-1 escalate-page failed ({e}) — skipped (fail-safe)", file=sys.stderr)
    return sent


def warn_console(fires) -> int:
    """STAGE-1 (op#13308, nazim-signed). NON-DESTRUCTIVE. For each WOULD-FIRE (worker lane
    >= PANE_FIRE_K at a clean idle), post a DEDUPED WARN to orch-console so a human/SRE
    recycles it before 100%. Returns count paged. No-op unless AUTO_RECYCLE_STAGE1_WARN=1.
    Dedup: skip a lane already warned within WARN_COOLDOWN_MIN. Fail-safe: any error logs +
    skips — a warn blip must never crash the observe pass or (it fires nothing) recycle."""
    if not STAGE1_WARN_ENABLED or not fires:
        return 0
    sent = 0
    try:
        import psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            for session, base, k, verdict, d in fires:
                agent = d.agent
                subject = f"WARN worker bloat: {agent} ~{k:.0f}k idle — recycle before 100%"
                for to_agent in WARN_RECIPIENTS:
                    cur.execute(
                        "SELECT 1 FROM agent_messages WHERE from_agent=%s AND to_agent=%s "
                        "AND subject LIKE %s AND created_at > now() - make_interval(mins => %s) LIMIT 1",
                        (bnd.SRE_AGENT_ID, to_agent, f"WARN worker bloat: {agent} %", WARN_COOLDOWN_MIN))
                    if cur.fetchone():
                        continue   # this recipient already warned for this lane within cooldown
                    if to_agent == "cc-fleet-health":
                        body = (
                            f"ACT (Stage-1.5, op#13308): worker lane {agent} (session {session}) is at "
                            f"~{k:.0f}k pane-truth (>= {PANE_FIRE_K}k, ~65% of 1M) and CLEAN IDLE. Close the "
                            f"loop at SRE: (1) SELF-CHECKPOINT the lane first — WRITE a fresh handoff yourself, "
                            f"NEVER trust an on-disk handoff's freshness (the 4-day-stale bug); (2) but FIRST "
                            f"check op#13323 — if it's idle-with-a-PENDING-PLAN (live monitor / pending bus item), "
                            f"checkpoint-first or leave-until-unblocked, NEVER blind-recycle; (3) checkpoint-first "
                            f"recycle via scripts/sre_lane_recycle.py; (4) CC orch-console. Deduped {WARN_COOLDOWN_MIN}m/lane.")
                    else:
                        body = (
                            f"FYI (non-blocking): worker lane {agent} (session {session}) ~{k:.0f}k idle >= fire "
                            f"bar — cc-fleet-health (SRE) is paged to self-checkpoint + checkpoint-first recycle it "
                            f"before 100%. Non-destructive Stage-1 WARN; loop closes at SRE, not you/the operator.")
                    if STAGE1_WARN_DRY:
                        print(f"[auto-recycle STAGE-1 WARN DRY] would page {to_agent} :: {subject}")
                    else:
                        cur.execute(
                            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
                            "VALUES (%s,%s,'update',%s,%s,false,'P2')",
                            (bnd.SRE_AGENT_ID, to_agent, subject, body))
                    sent += 1
            if not STAGE1_WARN_DRY:
                c.commit()
        if sent:
            print(f"[auto-recycle STAGE-1 WARN{' DRY' if STAGE1_WARN_DRY else ''}] "
                  f"paged console for {sent} bloated-idle worker lane(s)")
    except Exception as e:  # noqa: BLE001 — warn is best-effort; never crash the pass / fire anything
        print(f"[auto-recycle] WARN stage-1 page failed ({e}) — skipped (fail-safe)", file=sys.stderr)
    return sent


def run_stage0() -> int:
    """DETECT-ONLY sweep. Fires NOTHING. Logs, per red body, what it WOULD do — crucially
    the SELF-COMPACTED (G2 save) cases, which are the proof gate-2 works (op#18526 MUST-C)."""
    singletons = frozenset(bnd.SINGLETON_BODIES)
    worker_bases = _worker_bases_from_fleet_lanes()
    reds = [a for a in chw.read_context_gauge() if a.pct >= FIRE_THRESHOLD]
    print(f"[auto-recycle STAGE-0 detect-only] {len(reds)} body(ies) >= {FIRE_THRESHOLD}% fire-threshold; FIRING NOTHING")
    for a in reds:
        fresh = _fresh_pct(a.agent)                       # G2: fresh read, last step
        # G1 idle unknown in Stage-0 core (no tmux dependency here) -> None => GATED unless self-compacted/singleton
        d = classify(a, singletons, worker_bases, fresh, idle=None)
        print(f"  [{d.verdict}] {d.agent} ({d.pct}% detect): {d.reason}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="auto-recycle-on-bloat STAGE-0 (detect-only; fires nothing)")
    ap.add_argument("--dry-run", action="store_true", default=True, help="(Stage-0 is always dry-run)")
    ap.add_argument("--gauge", action="store_true",
                    help="run the LEGACY gauge-based pass instead of the pane pass (A/B compare)")
    ap.add_argument("--no-publish", action="store_true",
                    help="skip the op#13050-B pane_context publish (detection only)")
    args = ap.parse_args()
    if args.gauge:
        raise SystemExit(run_stage0())
    # Default daemon cycle: publish fresh pane-truth (op#13050-B, for the console's honest
    # header) THEN run the detect-only observe pass. Publish is best-effort + fail-safe.
    if not args.no_publish:
        publish_pane_context()
    raise SystemExit(run_observe_panes())
