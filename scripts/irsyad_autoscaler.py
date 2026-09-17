#!/usr/bin/env python3
"""irsyad-autoscaler — INERT detect+log, plus a SUPERVISED-PROPOSE arm (no actuation).

Design: docs/irsyad-autoscaler-design-v1.md. Two modes, selected by IRSYAD_AUTOSCALER_MODE
(default 'inert') or --mode:
  * 'inert'      — §6 rollout step 1: MEASURE demand, COMPUTE the would-spin / would-kill decision
                   under the §5 hard interlocks, LOG one row per tick to fleet_lane_autoscale_log.
                   Never spins/kills, never calls lanes.sh, never invokes lane_winddown as an actuator.
  * 'supervised' — Nazim #40401 gate. Everything 'inert' does, PLUS: when the pure decision says
                   would_spin, POST one deduped propose-then-confirm bus row to orch-console (P1 rr)
                   and stop. It STILL never spins and never kills — spin execution is a separate
                   Nazim-confirm-gated, wet-proved step (the elastic cc-irsyad-<N> worker-boot path
                   does not exist yet; actuating blind would be unsafe). So 'supervised' is the SAFE
                   half of the arm: detection + proposal. It emits nothing while the coord queue is
                   absent (demand=0). Kill stays idle-proof DETECT-ONLY in both modes.

Two layers, deliberately separated so the interlocks are testable without a DB or a tmux server:
  * `decide(...)` is a PURE function over injected observations. All of tests/test_irsyad_autoscaler.py
    exercises it against synthetic inputs.
  * the `probe_*` / `run_tick` functions are the LIVE side — they read the substrate read-only
    (and reuse lane_winddown's tested idle-proof), feed the pure function, and INSERT the log row.

§2 DEMAND — BOTH counts computed & logged every tick (which is canonical is Nazim's open Q1):
  * coord_queue_depth: depth of the coord-owned claimable dispatch queue. Coord owns that
    schema and it does NOT exist yet, so we read it DEFENSIVELY — a missing table is depth 0
    (source='absent'); a read ERROR on an existing table is UNREADABLE => NULL => fail-safe.
    This is the signal that DRIVES the spin decision (§2 option-1, the preferred canonical source).
  * option2_count: the §2 option-2 fallback — agent_messages to the `cc-irsyad*` family that are
    requires_response + unresponded past a grace and NOT owned by a live lane. Logged in parallel
    so a later wet-prove can compare it against the coord signal.

§5 INTERLOCKS enforced inside `decide`:
  * MAX_LANES cap (2) — never propose a spin that would exceed it.
  * SPIN_THRESHOLD (1) — spin only when demand >= threshold.
  * PROTECTED set as an ALLOW-LIST of auto-killable lanes (never a deny-list): only distinct-
    identity `cc-irsyad-<worker>` lanes are eligible; coord, the bare `cc-irsyad` client agent,
    any client-poller (owns a bot_channel), any money-path lane, and singletons are excluded
    BY CONSTRUCTION and can never enter the would-kill set.
  * idle-proof — a lane is a would-kill candidate only if provably IDLE; unprovable => skipped.
  * demand-pending holdoff — never propose shrinking the pool while demand is pending.
  * atomic check-and-claim is the ACTUATION primitive (not exercised here); noted only.
  * FAIL-SAFE — on ANY ambiguity (demand signal unreadable) decide NOTHING (no spin, no kill).
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── §5 constants ────────────────────────────────────────────────────────────────────────
MAX_LANES = 2               # design §5 / Musa op19217 — hard cap on the elastic pool
SPIN_THRESHOLD = 1          # design §5 / §2 — spin when unclaimed demand >= this
CLAIM_GRACE_SECONDS = 120   # "past a short grace" — ignore work younger than this (§2)

# The elastic pool is the distinct-identity worker family `cc-irsyad-<worker>` (design §3 —
# distinct identity per lane, NOT the colliding bare `cc-irsyad`). The trailing hyphen is
# load-bearing: it EXCLUDES the bare `cc-irsyad` standing client agent, which is protected.
POOL_PREFIX = "cc-irsyad-"
# Exact base ids that share the pool prefix but are PROTECTED (never auto-killable).
PROTECTED_BASE_IDS = frozenset({"cc-irsyad-coord"})  # coord: supervised coordinator + poller owner

# option-2 measurement bound (Nazim 39345 ruling-1): a 24h ROLLING window + work-ticket types
# only (exclude 'update'/'agreed' status/ack chatter). Tunable; option-1 (coord queue) is canonical.
OPTION2_WINDOW_HOURS = 24
OPTION2_EXCLUDE_TYPES = ("update", "agreed")
# Coarse money-path markers scanned in a lane's notes — over-protect (a false positive only
# ever REMOVES a lane from the kill set, the safe direction for an allow-list).
MONEY_MARKERS = ("money", "giro", "tabung-fajr", "payment", "payout", "bank-transfer")

# The coord-owned claimable dispatch queue table. SCHEMA ASSUMPTION (coord owns it, does not
# exist yet): a claimable queue with a nullable `claimed_by` and a `created_at` timestamptz;
# unclaimed depth = rows with claimed_by IS NULL older than the grace. Overridable via env so
# coord can name it whatever it likes without a code change.
COORD_QUEUE_TABLE = os.environ.get("IRSYAD_COORD_QUEUE_TABLE", "coord_dispatch_queue")

LIVE_HEARTBEAT_WINDOW = "30 minutes"  # what counts as a live lane in agent_status

# ── §6 SUPERVISED arm — PROPOSE layer (Nazim #40401 gate) ─────────────────────────────────
# Mode selector. Default 'inert' = the original detect+log behaviour, ZERO change. 'supervised'
# adds ONE thing: when the pure decision says would_spin, POST a deduped propose-then-confirm
# bus row to orch-console (P1 rr) and STOP. It NEVER auto-spins and NEVER kills — spin execution
# stays a Nazim-confirm-gated, separately-wet-proved step (the elastic cc-irsyad-<N> worker-boot
# path does not exist yet; actuating blind would be unsafe). So 'supervised' is the safe half of
# the arm: detection + proposal. While the coord queue is absent (demand=0) it emits nothing.
AUTOSCALER_MODE = os.environ.get("IRSYAD_AUTOSCALER_MODE", "inert").strip().lower()
# Dedup window: never re-propose while a proposal to orch-console is still outstanding.
PROPOSAL_TTL_MIN = int(os.environ.get("IRSYAD_PROPOSAL_TTL_MIN", "60"))
PROPOSAL_SUBJECT_PREFIX = "[irsyad-autoscaler] SPIN proposal"


# ── data shapes ───────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LaneObs:
    """One live lane, as observed. All facts injected so `decide` stays pure and testable."""
    lane: str                 # tmux session / lane key
    base_agent_id: str        # family id (agents.id)
    agent_id: str             # live sub-tag id
    live: bool                # heartbeat fresh + not offline
    idle: Optional[bool]      # provably idle? None = COULD NOT PROVE (fail-safe: never killed)
    owns_bot_channel: bool = False   # client-poller => protected
    money_path: bool = False         # money-path => protected
    idle_reason: str = ""


@dataclass
class Decision:
    coord_queue_depth: Optional[int]
    coord_queue_source: str
    coord_queue_readable: bool
    option2_count: int
    pool_size: int
    demand: Optional[int]
    would_spin: bool
    spin_target: Optional[str]
    would_kill: List[dict]
    interlocks: dict
    reason: str
    ambiguous: bool
    host: str = ""


# A numbered cc-irsyad SUB-TAG (cc-irsyad-1, cc-irsyad-3, …). The real identity model is a
# SHARED base 'cc-irsyad' with a distinct sub-tag per body (agent_status.agent_id) — NOT a
# distinct base per worker. So pool membership keys on the AGENT_ID, not base_agent_id. This
# matches cc-irsyad-<N> and EXCLUDES cc-irsyad-coord-<N> (has 'coord', not a digit, after the
# prefix) and the bare 'cc-irsyad'. (Fixes the #40672 miscount: is_auto_killable checked
# base_agent_id.startswith('cc-irsyad-'), but the base is exactly 'cc-irsyad' → it read pool=0
# while numbered workers were live → over-proposed a spin past MAX_LANES.)
POOL_SUBTAG_RE = re.compile(r"^cc-irsyad-\d+$")
# Spun elastic-worker lanes live in tmux sessions named 'irsyad-worker-<N>' (the actuator's pool).
# Standing cc-irsyad lanes (tabung=irsyad-tabung-jumaat, coord=irsyad-coord) are NOT elastic
# workers — excluded from BOTH the cap and kill-eligibility (Nazim #40850 ruling (a)).
WORKER_SESSION_PREFIX = "irsyad-worker-"
WORKER_SESSION_RE = re.compile(r"^irsyad-worker-\d+$")


def is_elastic_worker(lane_session: str) -> bool:
    """CAP membership (Nazim #40850 ruling (a)): MAX_LANES counts ONLY spun elastic workers —
    tmux sessions 'irsyad-worker-<N>'. STANDING lanes (tabung=irsyad-tabung-jumaat, coord=
    irsyad-coord, any future standing irsyad lane) are EXCLUDED from the cap AND from kill. So the
    effective elastic capacity is a full MAX_LANES concurrent workers regardless of standing lanes.
    (This supersedes the #40672 sub-tag count, which wrongly let the standing tabung lane eat a slot.)"""
    return bool(WORKER_SESSION_RE.match(lane_session or ""))


def is_pool_member(agent_id: str) -> bool:
    """A numbered cc-irsyad sub-tag (cc-irsyad-<N>). Retained for identity checks; the CAP no
    longer uses this (see is_elastic_worker) — a numbered sub-tag alone includes standing lanes."""
    return bool(POOL_SUBTAG_RE.match(agent_id or ""))


def is_auto_killable(agent_id: str, lane_session: str, owns_bot_channel: bool, money_path: bool) -> bool:
    """KILL eligibility (allow-list): a lane may be auto-wound-down ONLY if it is a SPUN elastic
    worker (session 'irsyad-worker-<N>') carrying a numbered cc-irsyad sub-tag AND no protected
    condition holds. Standing lanes (tabung), coord, pollers, money-path are excluded by
    construction. A NEW/unknown lane defaults to protected."""
    if not is_pool_member(agent_id):
        return False                       # not a numbered cc-irsyad body (bare/coord/singleton)
    if not (lane_session or "").startswith(WORKER_SESSION_PREFIX):
        return False                       # standing cc-irsyad lane (e.g. tabung) — count-only, never killed
    if owns_bot_channel:
        return False                       # client-poller
    if money_path:
        return False                       # money-path lane
    return True


def should_emit_proposal(mode: str, would_spin: bool, ambiguous: bool,
                         open_proposal_exists: bool) -> bool:
    """PURE: propose a spin iff SUPERVISED mode AND a real (non-ambiguous) would-spin AND no
    proposal is already outstanding (dedup). No side effects — the DB read (open_proposal_exists)
    and the bus write both live in the caller, so this stays unit-testable without a DB."""
    return (mode == "supervised") and would_spin and (not ambiguous) and (not open_proposal_exists)


def decide(
    coord_queue_depth: Optional[int],
    coord_queue_readable: bool,
    coord_queue_source: str,
    option2_count: int,
    lanes: List[LaneObs],
    host: str = "",
) -> Decision:
    """Pure §5 decision logic. `coord_queue_depth` is the demand driver; `coord_queue_readable`
    is False ONLY on a genuine read error (a missing table is readable, depth 0). All lane facts
    arrive via `lanes`. Returns the fully-populated INERT decision — no side effects."""
    interlocks: dict = {}

    # Pool (for the CAP) = LIVE numbered cc-irsyad bodies (agent_id ~ cc-irsyad-<N>): spun
    # elastic workers AND any standing cc-irsyad-<N> lane (tabung). Keys on the sub-tag, the real
    # identity — NOT base_agent_id (all share the bare 'cc-irsyad' base). Coord (cc-irsyad-coord-N)
    # is excluded. This is the #40672 fix: it now counts live workers instead of reading 0.
    pool = [l for l in lanes if l.live and is_elastic_worker(l.lane)]
    pool_size = len(pool)
    interlocks["protected_allowlist"] = (
        "cap counts ONLY spun elastic workers (irsyad-worker-<N> sessions); standing lanes "
        "(tabung/coord/any standing irsyad) excluded from cap AND kill (Nazim #40850 ruling (a))")
    interlocks["max_lanes_cap"] = f"MAX_LANES={MAX_LANES}; pool={pool_size} ({[l.agent_id for l in pool]})"

    # ── FAIL-SAFE: any ambiguity in the demand signal => decide NOTHING ──────────────────
    ambiguous = not coord_queue_readable
    demand = coord_queue_depth if coord_queue_readable else None
    if ambiguous:
        interlocks["failsafe"] = (
            "TRIGGERED — coord-queue signal UNREADABLE (table present, query errored); "
            "decide NOTHING (no spin, no kill)")
        interlocks["spin_threshold"] = "not evaluated (ambiguous)"
        interlocks["idle_proof"] = "not evaluated (ambiguous)"
        interlocks["demand_pending_holdoff"] = "not evaluated (ambiguous)"
        return Decision(
            coord_queue_depth=coord_queue_depth, coord_queue_source=coord_queue_source,
            coord_queue_readable=coord_queue_readable, option2_count=option2_count,
            pool_size=pool_size, demand=None, would_spin=False, spin_target=None,
            would_kill=[], interlocks=interlocks,
            reason=("FAIL-SAFE: demand signal unreadable — decided NOTHING "
                    f"(option2_count={option2_count}, pool={pool_size})"),
            ambiguous=True, host=host)

    interlocks["failsafe"] = "clear (demand signal readable)"

    # ── SPIN decision: demand >= threshold AND under the cap ─────────────────────────────
    meets_threshold = demand >= SPIN_THRESHOLD
    under_cap = pool_size < MAX_LANES
    interlocks["spin_threshold"] = f"demand={demand} >= SPIN_THRESHOLD={SPIN_THRESHOLD}: {meets_threshold}"
    interlocks["max_lanes_cap"] = f"pool={pool_size} < MAX_LANES={MAX_LANES}: {under_cap}"
    would_spin = bool(meets_threshold and under_cap)
    spin_target = (f"propose +1 cc-irsyad worker (pool {pool_size} -> {pool_size + 1}); "
                   "actuation primitive = atomic check-and-claim on fleet_lanes (not armed)"
                   ) if would_spin else None

    # ── KILL decision: idle-proof + demand-pending holdoff ───────────────────────────────
    would_kill: List[dict] = []
    demand_pending = demand >= SPIN_THRESHOLD
    interlocks["demand_pending_holdoff"] = (
        f"demand={demand} pending: {demand_pending} — no shrink while work waits"
        if demand_pending else f"demand={demand}: no pending work, wind-down eligible")
    if demand_pending:
        interlocks["idle_proof"] = "not evaluated — demand pending, holdoff active"
    else:
        provably_idle = 0
        for l in pool:
            # Kill-eligibility is the STRICTER subset of the cap pool: only SPUN elastic workers
            # (irsyad-worker-<N> sessions) — never a standing lane (tabung) that merely counts
            # toward the cap. is_auto_killable enforces session + poller + money guards.
            if l.idle is True and is_auto_killable(l.agent_id, l.lane, l.owns_bot_channel, l.money_path):
                provably_idle += 1
                would_kill.append({
                    "lane": l.lane,
                    "base_agent_id": l.base_agent_id,
                    "agent_id": l.agent_id,
                    "reason": ("auto-killable pool worker PROVABLY IDLE "
                               f"({l.idle_reason or 'idle-proof passed'}) — would wind down; "
                               "actuation primitive = atomic check-and-claim (not armed)"),
                })
            # l.idle is None (unprovable) or False => never a candidate (fail-safe).
        interlocks["idle_proof"] = (
            f"{provably_idle} of {pool_size} pool lane(s) PROVABLY idle; "
            "unprovable/busy lanes excluded (fail-safe)")

    # ── one-line verdict ─────────────────────────────────────────────────────────────────
    bits = []
    bits.append(f"SPIN +1 ({spin_target.split(';')[0]})" if would_spin else "no spin")
    bits.append(f"KILL {len(would_kill)}" if would_kill else "no kill")
    reason = (f"demand={demand} option2={option2_count} pool={pool_size}/{MAX_LANES} "
              f"[coord_queue={coord_queue_source}] -> " + ", ".join(bits))

    return Decision(
        coord_queue_depth=coord_queue_depth, coord_queue_source=coord_queue_source,
        coord_queue_readable=coord_queue_readable, option2_count=option2_count,
        pool_size=pool_size, demand=demand, would_spin=would_spin, spin_target=spin_target,
        would_kill=would_kill, interlocks=interlocks, reason=reason, ambiguous=False, host=host)


# ══════════════════════════════════════════════════════════════════════════════════════
# LIVE side — read-only substrate probes + the INERT tick. None of this actuates.
# ══════════════════════════════════════════════════════════════════════════════════════
def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                return line.split("=", 1)[1].strip()
    raise SystemExit("irsyad_autoscaler: no DATABASE_URL")


def probe_coord_queue_depth(conn, grace_s: int = CLAIM_GRACE_SECONDS):
    """(depth, readable, source). DEFENSIVE per design §2: a MISSING table is depth 0
    (source='absent', readable=True — coord has not built the queue yet); a read ERROR on an
    existing table is UNREADABLE (None, False) => the tick fails safe (no spin, no kill)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{COORD_QUEUE_TABLE}",))
            if cur.fetchone()[0] is None:
                return 0, True, "absent"
            cur.execute(
                f"SELECT count(*) FROM public.{COORD_QUEUE_TABLE} "
                "WHERE claimed_by IS NULL AND created_at < now() - (%s * interval '1 second')",
                (grace_s,))
            return int(cur.fetchone()[0]), True, COORD_QUEUE_TABLE
    except Exception as e:  # existing table, unreadable shape/perms => fail-safe
        return None, False, f"{COORD_QUEUE_TABLE} (read error: {type(e).__name__})"


def probe_live_agent_ids(conn) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agent_id FROM agent_status "
            f"WHERE last_heartbeat > now() - interval '{LIVE_HEARTBEAT_WINDOW}' "
            "AND status <> 'offline'")
        return [r[0] for r in cur.fetchall()]


def probe_option2_count(conn, live_agent_ids: List[str], grace_s: int = CLAIM_GRACE_SECONDS) -> int:
    """§2 option-2: requires_response + unresponded agent_messages to the cc-irsyad* family,
    past the grace, NOT owned by a live lane (unclaimed, or claimed by a now-dead agent).

    Nazim 39345 ruling-1: BOUND this to ACTIONABLE RECENT demand for the INERT wet-prove
    comparison against coord's queue — a 24h ROLLING window (upper bound) + work-ticket
    message_types only (exclude 'update'/'agreed' status-and-ack chatter). This turns the raw
    unbounded 761 (all-time noise, which is exactly why agent_messages can't be canonical) into
    a meaningful 'recent unclaimed demand' figure. Measurement-only — stays INERT."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM agent_messages "
            "WHERE to_agent LIKE 'cc-irsyad%%' "
            "AND requires_response = true AND responded_at IS NULL "
            "AND coalesce(is_test, false) = false "
            "AND message_type <> ALL(%s) "
            "AND created_at < now() - (%s * interval '1 second') "
            f"AND created_at > now() - interval '{OPTION2_WINDOW_HOURS} hours' "
            "AND (claimed_by IS NULL OR claimed_by <> ALL(%s))",
            (list(OPTION2_EXCLUDE_TYPES), grace_s, live_agent_ids or [""]))
        return int(cur.fetchone()[0])


def probe_pool_lanes(conn, host: str) -> List[LaneObs]:
    """Live irsyad worker lanes + their idle verdict. Idle-proof reuses lane_winddown's TESTED
    predicate (the same gate the eventual actuator would use): idle=True iff it says wind-down-
    OK, else False; a lane whose tmux is unreachable fails closed to not-idle => never killed."""
    from scripts.lib import lane_winddown as lw
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agent_id, base_agent_id, tmux_session, host FROM agent_status "
            f"WHERE last_heartbeat > now() - interval '{LIVE_HEARTBEAT_WINDOW}' "
            "AND status <> 'offline' AND base_agent_id LIKE 'cc-irsyad%%'")
        rows = cur.fetchall()
        # client-poller signal: the set of lane/agent identifiers any ENABLED bot_channel is
        # wired to — inject_target (the tmux target a channel injects into), responder_ref, and
        # group_routing->>'agent_reviewer'. A pool lane whose session/agent_id/base appears here
        # OWNS a client channel => protected. Fail-safe: on any read error, protect EVERY
        # candidate (owns_poller=True) rather than risk proposing to kill a live client poller.
        poller_refs: set = set()
        poller_readable = True
        try:
            cur.execute("SELECT to_regclass('public.bot_channels')")
            if cur.fetchone()[0] is not None:
                cur.execute(
                    "SELECT coalesce(inject_target,''), coalesce(responder_ref,''), "
                    "coalesce(group_routing->>'agent_reviewer','') "
                    "FROM bot_channels WHERE enabled = true")
                for it, rr, rev in cur.fetchall():
                    poller_refs.update(x for x in (it, rr, rev) if x)
        except Exception:
            poller_readable = False

    out: List[LaneObs] = []
    for agent_id, base, session, lane_host in rows:
        if base in PROTECTED_BASE_IDS:
            continue  # coord never enters the pool
        session = session or agent_id
        # money-path: coarse notes scan (over-protect, safe direction).
        money = _lane_notes_money(conn, session)
        # client-poller: fail-safe True when unreadable, else membership in the channel ref set.
        owns_poller = (not poller_readable) or bool(
            {session, agent_id, base} & poller_refs)
        # idle-proof via the tested predicate (fails closed to not-idle if tmux unreachable).
        try:
            ok, why = lw.may_wind_down(
                session,
                session_exists=lw.live_session_exists,
                is_busy=lw.live_is_busy,
                unread_count=lw.live_unread_count,
                handoff_age_s=lw.live_handoff_age_s,
                composer_state=lw.live_composer_state)
            idle, idle_reason = (True, why) if ok else (False, why)
        except Exception as e:
            idle, idle_reason = None, f"idle-proof error: {type(e).__name__}"
        out.append(LaneObs(
            lane=session, base_agent_id=base, agent_id=agent_id, live=True,
            idle=idle, owns_bot_channel=owns_poller, money_path=money, idle_reason=idle_reason))
    return out


def _lane_notes_money(conn, lane: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT coalesce(notes,'') FROM fleet_lanes WHERE lane=%s", (lane,))
            row = cur.fetchone()
            notes = (row[0] if row else "").lower()
            return any(m in notes for m in MONEY_MARKERS)
    except Exception:
        return True  # unreadable => protect (over-protect is the safe direction)


def gather_and_decide(conn, host: str) -> Decision:
    depth, readable, source = probe_coord_queue_depth(conn)
    live_ids = probe_live_agent_ids(conn)
    option2 = probe_option2_count(conn, live_ids)
    lanes = probe_pool_lanes(conn, host)
    return decide(depth, readable, source, option2, lanes, host=host)


def write_log_row(conn, d: Decision) -> Optional[int]:
    """INSERT the INERT decision row. Returns the new id, or None if the log table does not
    exist yet (the migration is applied by the hub post-review — until then the tick prints
    the decision and writes nothing, so it can soak-run harmlessly)."""
    import json
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.fleet_lane_autoscale_log')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute(
            "INSERT INTO fleet_lane_autoscale_log "
            "(host, coord_queue_depth, coord_queue_source, option2_count, pool_size, demand, "
            " would_spin, spin_target, would_kill_candidates, interlocks, decision_reason, "
            " ambiguous, inert) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,true) RETURNING id",
            (d.host, d.coord_queue_depth, d.coord_queue_source, d.option2_count, d.pool_size,
             d.demand, d.would_spin, d.spin_target, json.dumps(d.would_kill),
             json.dumps(d.interlocks), d.reason, d.ambiguous))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def _open_spin_proposal_exists(conn) -> bool:
    """True if a spin proposal to orch-console is still outstanding (un-answered) within the TTL.
    Fail-safe: on ANY read error, return True (assume one is open) so we do NOT double-propose."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_messages "
                "WHERE from_agent='cc-orchestrator' AND to_agent='orch-console' "
                "AND subject LIKE %s AND responded_at IS NULL "
                "AND created_at > now() - (%s * interval '1 minute') LIMIT 1",
                (PROPOSAL_SUBJECT_PREFIX + "%", PROPOSAL_TTL_MIN))
            return cur.fetchone() is not None
    except Exception:
        return True


def emit_spin_proposal(conn, d: Decision) -> Optional[int]:
    """SUPERVISED-propose: post ONE P1 rr spin proposal to orch-console. NEVER actuates — the
    spin is executed only on Nazim's confirm, as a separate (wet-proved) step. Returns the new
    bus id, or None on failure."""
    subject = (f"{PROPOSAL_SUBJECT_PREFIX}: demand={d.demand} pool={d.pool_size}/{MAX_LANES} "
               f"on {d.host} — CONFIRM to spin +1 irsyad worker")
    body = (
        f"AUTOSCALER (SUPERVISED-propose, Nazim #40401 gate) — coord queue "
        f"'{d.coord_queue_source}' shows unclaimed demand={d.demand} (>= threshold "
        f"{SPIN_THRESHOLD}) and pool={d.pool_size} < MAX_LANES={MAX_LANES}.\n\n"
        "PROPOSAL: spin +1 elastic irsyad worker (cc-irsyad-<N>, musa2 key, gzb).\n"
        f"Interlocks at propose time: {d.interlocks}\n\n"
        "Reply to CONFIRM (the hub spins on your confirm) or DECLINE. I do NOT auto-spin — this "
        "is the propose-then-confirm gate you set. Kill path stays idle-proof detect-only this phase."
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                "requires_response,priority) "
                "VALUES ('cc-orchestrator','orch-console','question',%s,%s,true,'P1') RETURNING id",
                (subject, body))
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        print(f"    SUPERVISED: proposal emit failed ({type(e).__name__}: {e})")
        return None


def run_tick(write: bool = True) -> Decision:
    import psycopg
    host = socket.gethostname()
    tag = {"supervised": "SUPERVISED", "auto": "AUTO"}.get(AUTOSCALER_MODE, "INERT")
    with psycopg.connect(_dsn()) as conn:
        d = gather_and_decide(conn, host)
        logged = write_log_row(conn, d) if write else None
        if AUTOSCALER_MODE == "auto" and d.would_spin and not d.ambiguous:
            # FULLY-AUTO (Nazim #40850): pool-safe demand spins WITHOUT a confirm. The actuator
            # re-checks would_spin (demand-gate) + MAX_LANES before booting, so this spins ONE
            # worker per tick and stops at the cap (re-eval after each boot, #40854 (3)).
            import subprocess as _sp
            print("    AUTO: would_spin=True -> actuating irsyad_spin_worker --auto (no confirm)")
            _sp.run([sys.executable, str(_ROOT / "scripts" / "irsyad_spin_worker.py"), "--auto"])
        elif should_emit_proposal(AUTOSCALER_MODE, d.would_spin, d.ambiguous,
                                  _open_spin_proposal_exists(conn)):
            # SUPERVISED-propose arm: detect + propose only (no actuation). Gated + deduped.
            pid = emit_spin_proposal(conn, d)
            if pid is not None:
                print(f"    SUPERVISED: spin proposal posted to orch-console (bus #{pid}) — "
                      "awaiting Nazim confirm; NO auto-spin")
        elif AUTOSCALER_MODE == "supervised" and d.would_spin and not d.ambiguous:
            print("    SUPERVISED: would-spin, but a proposal is already outstanding (dedup) — no re-post")
    print(f"[irsyad-autoscaler {tag}] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
          f"{d.reason}")
    if d.would_kill:
        for c in d.would_kill:
            print(f"    would-kill: {c['lane']} ({c['base_agent_id']}) — {c['reason']} "
                  "(detect-only — kill not armed)")
    if write and logged is None:
        print("    (fleet_lane_autoscale_log absent — decision NOT persisted; migration 062 "
              "applied by the hub post-review. Detection ran; log invariant intact.)")
    elif logged is not None:
        print(f"    logged id={logged}")
    return d


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="irsyad-autoscaler INERT tick (detect+log ONLY).")
    ap.add_argument("--once", action="store_true", help="run a single tick (default)")
    ap.add_argument("--loop", action="store_true", help="run forever on --interval")
    ap.add_argument("--interval", type=int, default=300, help="seconds between ticks in --loop")
    ap.add_argument("--no-write", action="store_true",
                    help="compute + print the decision but do not write the log row")
    ap.add_argument("--mode", choices=("inert", "supervised", "auto"), default=None,
                    help="override IRSYAD_AUTOSCALER_MODE (inert=detect+log only; "
                         "supervised=post deduped spin proposals, spin on confirm; "
                         "auto=fully-auto, spin directly on pool-safe would_spin, no confirm)")
    args = ap.parse_args(argv)
    if args.mode:
        global AUTOSCALER_MODE
        AUTOSCALER_MODE = args.mode
    write = not args.no_write
    tag = "SUPERVISED" if AUTOSCALER_MODE == "supervised" else "INERT"
    if args.loop:
        print(f"[irsyad-autoscaler {tag}] loop up (interval {args.interval}s) — "
              + ("detect+log+propose (no actuation)" if tag == "SUPERVISED" else "detect+log only"))
        while True:
            try:
                run_tick(write=write)
            except Exception as e:  # never die on a transient DB blip
                print(f"[irsyad-autoscaler INERT] tick error ({type(e).__name__}): {e}")
            time.sleep(args.interval)
    run_tick(write=write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
