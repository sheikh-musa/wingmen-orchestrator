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
import sys
from dataclasses import dataclass

from scripts import context_health_watchdog as chw
from scripts.lib import fleet_health_boundaries as bnd

FIRE_THRESHOLD = 88      # auto-recycle FIRE bar (distinct from ~80% alert-red)
HARD_LINE = 95           # shortens G1 idle-patience only; never touches G2 / never fires busy


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
    ap.parse_args()
    raise SystemExit(run_stage0())
