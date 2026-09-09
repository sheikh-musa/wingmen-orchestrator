#!/usr/bin/env python3
"""lane_proactive_recycle_nudge.py — PROACTIVE self-recycle NUDGE tier (op#19141, Nazim 38313 Opt A).

The proactive half the dead-man's own docstring defers to ("interim; the full ~80% loop does proper
per-episode + grace"). It closes the gap a continuously-BUSY bloating lane falls through: every
PROACTIVE detector today gates on a clean IDLE (auto_recycle_on_bloat, lane_selfrecycle_detect), so a
busy lane is invisible to all of them and only the dead-man reaches it — late (85% sustained >=30 min,
already ~91%) and once/day. Verified on irsyad-coord 2026-09-09: it rode ~84%->94.6% while busy on
#677, GATED every proactive tick, and the dead-man was the ONLY thing that nudged it.

THIS TIER nudges at a LOWER ~80% bar, BUSY-INCLUSIVE, and RE-NUDGES on continued climb (per-episode +
grace, escalating), so a deferred-but-still-bloating lane is re-prompted rather than silent till
tomorrow. Busy-inclusive is SAFE because the action is a non-destructive MESSAGE: the lane always
writes its own handoff, decides, and picks its own seam — a nudge never interrupts busy work the way a
forced external recycle would.

NON-DESTRUCTIVE BY CONSTRUCTION (Nazim's gate condition): this module has NO recycle / reset /
send-keys / tmux path whatsoever — it only INSERTs one agent_messages row. It is STRUCTURALLY unable
to execute a recycle. It cannot auto-recycle a busy lane; the lane alone acts.

SCOPE — worker/client lanes ONLY (never core infra): discover_lanes already excludes the singletons
(cc-orchestrator / cai / orch-console / cc-fleet-health), and assert_sre_never_targets_singleton
re-asserts per lane (fail-closed defence in depth).

DEAD-MAN UNTOUCHED (condition c): lane_recycle_deadman.py is not modified — its 85%-sustained PAGE to
orch-console stays the true last-resort human backstop. To keep the two tiers from stacking, the send
is deduped against BOTH nudge subject prefixes within a cooldown.

LEASE-GATED (charter §3): the SEND is gated on fleet_health_lease.gate() — while the hub holds a
reclaimed lease this tier downgrades to scan + log (no send), same fail-closed-for-non-holder shape as
priority_sla_watchdog. (This differs from the ungated dead-man sibling — deliberately, per §3: a
self-healing ACTION is lease-gated; detection stays ungated.)

DEPLOYS INERT: sends only when PROACTIVE_RECYCLE_NUDGE_ENABLED=1 (else it scans + logs WOULD-NUDGE,
never sends, never mutates state). Its launchd job ships NOT loaded until the arm is gated.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

from scripts import lane_recycle_deadman as dm       # REUSE the proven reading path (no divergence)
from scripts import sre_lane_recycle as slr           # discover_lanes
from scripts.lib import fleet_health_boundaries as fhb

# ── thresholds (env-overridable) ─────────────────────────────────────────────
PROACTIVE_PCT = int(os.environ.get("PROACTIVE_NUDGE_PCT", "80"))   # proactive bar, BELOW the 85 backstop
RENUDGE_DELTA = int(os.environ.get("PROACTIVE_RENUDGE_DELTA", "5"))  # re-nudge only after +this many pct
GRACE_S = int(os.environ.get("PROACTIVE_GRACE_S", "1800"))          # ...AND >= this long since last nudge
MAX_NUDGES = int(os.environ.get("PROACTIVE_MAX_NUDGES", "3"))       # cap/episode, then dead-man page owns it
COOLDOWN_MIN = int(os.environ.get("PROACTIVE_COOLDOWN_MIN", str(GRACE_S // 60)))  # cross-tier dedup window
DEADMAN_PCT = dm.HARD_PCT                                           # 85 — the backstop bar, for the message
ENABLED = os.environ.get("PROACTIVE_RECYCLE_NUDGE_ENABLED") == "1"  # arm flag: deploys INERT until set

STATE_PATH = _ORCH_DIR / "state" / "lane_proactive_recycle_nudge.json"
SUBJECT_PREFIX = "[proactive-recycle-nudge]"       # this tier's dedup anchor — keep STABLE
DEADMAN_PREFIX = "[self-recycle-nudge]"            # the dead-man's anchor — for cross-tier dedup


# ── pure decision core (unit-tested; FIRES NOTHING) ──────────────────────────
def evaluate_proactive_nudge(known, pct, episode, now,
                             bar: int = PROACTIVE_PCT, grace_s: int = GRACE_S,
                             renudge_delta: int = RENUDGE_DELTA, max_nudges: int = MAX_NUDGES):
    """PURE. One lane's reading -> (verdict, new_episode).

    episode = None | {"first_over_at","last_nudge_at","last_nudge_pct","nudges"}.
    verdict:
      "ok"      below bar OR blind -> clear the episode (new_episode=None). The dead-man owns the
                blind/page_unknown path; we never proactively nudge on an unconfirmed reading.
      "nudge"   first crossing this episode -> emit the first proactive nudge; start the episode.
      "renudge" climbed >= renudge_delta AND >= grace_s since the last nudge -> escalating re-nudge.
      "hold"    over bar, episode active, but within grace / not climbed enough -> keep, no send.
      "cap"     over bar but already nudged max_nudges times -> keep, no send (dead-man page escalates).

    Busy-inclusive: idle is NOT an input. The action is a non-destructive message; the lane always
    decides + picks its own seam, so nudging a busy lane never interrupts its work.
    """
    if not known or pct is None or pct < bar:
        return "ok", None
    if episode is None:
        return "nudge", {"first_over_at": now, "last_nudge_at": now,
                         "last_nudge_pct": pct, "nudges": 1}
    nudges = int(episode.get("nudges", 1))
    if nudges >= max_nudges:
        return "cap", episode
    climbed = pct - float(episode.get("last_nudge_pct", pct))
    aged = now - float(episode.get("last_nudge_at", now))
    if climbed >= renudge_delta and aged >= grace_s:
        return "renudge", {**episode, "last_nudge_at": now,
                           "last_nudge_pct": pct, "nudges": nudges + 1}
    return "hold", episode


# ── pure message (unit-tested) ───────────────────────────────────────────────
def proactive_nudge_message(lane: str, pct, bar: int = PROACTIVE_PCT, nudge_n: int = 1,
                            deadman_pct: int = DEADMAN_PCT):
    """PURE (subject, body) for the proactive nudge. Shaped like the dead-man's hand-nudge that worked
    on cosem-exams: write-your-OWN-handoff FIRST, THEN self_recycle.sh, and the LANE decides. Escalates
    on a re-nudge (nudge_n > 1). Subject prefix `[proactive-recycle-nudge] {lane}:` is the dedup anchor
    — keep STABLE."""
    escalate = nudge_n > 1
    pct_s = f"~{pct}%" if pct is not None else "over the bar"
    subject = (f"{SUBJECT_PREFIX} {lane}: {pct_s} context and climbing"
               + (f" (nudge #{nudge_n}, still climbing)" if escalate else "")
               + " — write your own handoff then self_recycle.sh (you decide)")
    if escalate:
        lead = (f"TL;DR (nudge #{nudge_n} — you were nudged before and are STILL climbing, now {pct_s}): "
                f"please self-recycle at your next seam before you reach the context cliff. If you keep "
                f"grinding you risk auto-compacting and losing in-flight state mid-task.")
    else:
        lead = (f"TL;DR: you (lane '{lane}') are at {pct_s} context — past the ~{bar}% proactive bar — "
                f"and still climbing. Recycle at a clean seam SOON so you come back fresh, WELL before "
                f"the cliff. You don't have to stop mid-step; do it at your next natural boundary.")
    body = (
        f"{lead}\n\n"
        f"WHAT: write a fresh handoff of YOUR OWN open loops (you know them; I can't safely enumerate "
        f"or commit your in-flight state for you), THEN run `scripts/self_recycle.sh` — it is "
        f"lane-preserving (commits your work at your turn boundary, keeps your token/lease) and "
        f"/clear's you in place.\n"
        f"YOU DECIDE: if you're mid-critical where recycling now is worse than the bloat, you may "
        f"DECLINE and keep going — this is a proactive nudge, NOT a reset, and I never recycle you. "
        f"The SRE dead-man stays your backstop and pages a human only if you cross {deadman_pct}% and "
        f"stay there.\n"
        f"WHY THIS REACHES YOU (even though you may be busy): the external recycler structurally can't "
        f"touch a busy lane, so a non-destructive nudge is the only path that lets YOU close the loop "
        f"early — the whole point is to catch you at ~{bar}% instead of at the cliff."
    )
    return subject, body


# ── state I/O (own file; never the dead-man's) ───────────────────────────────
def _load_state() -> dict:
    try:
        import json
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    import json
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)  # atomic


# ── DB shell (never recycles; only detects + sends one message) ──────────────
def _nudged_recently(cur, lane: str, cooldown_min: int = COOLDOWN_MIN) -> bool:
    """Cross-tier dedup: True if EITHER this tier OR the dead-man already nudged `lane` within the
    cooldown, so the two tiers never stack two nudges on the same lane in the same window."""
    cur.execute(
        "SELECT 1 FROM agent_messages WHERE from_agent='cc-fleet-health' "
        "  AND (subject LIKE %s OR subject LIKE %s) "
        "  AND created_at > now() - make_interval(mins => %s) LIMIT 1",
        [f"{SUBJECT_PREFIX} {lane}:%", f"{DEADMAN_PREFIX} {lane}:%", cooldown_min])
    return cur.fetchone() is not None


def _send_nudge(cur, conn, lane_base: str, subject: str, body: str) -> None:
    """Insert the proactive nudge to the LANE (to_agent = base_agent_id, the deliverable id its wake
    subscriber listens on). P2 (proactive/softer than the dead-man's P1 backstop nudge). Benign,
    reversible message-send; never touches the lane's state."""
    cur.execute(
        "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
        " priority, requires_response, created_at) "
        "VALUES ('cc-fleet-health', %s, 'blocker', %s, %s, 'P2', false, now())",
        [lane_base, subject, body])
    conn.commit()


def _lease_ok() -> "tuple[bool, str]":
    """fleet_health_lease.gate() wrapper (monkeypatchable). Fail-SAFE: any check error -> holder
    (never strand the monitor); returns (False, reason) only on positive evidence the hub holds a
    fresh reclaimed lease."""
    try:
        from scripts.lib import fleet_health_lease as fhl
        return fhl.gate()
    except Exception as e:  # noqa: BLE001 — fail-safe
        return True, f"lease-check-failed-failsafe:{type(e).__name__}"


def run(conn, dry: bool = False) -> int:
    """DETECT + proactive-nudge sweep over worker lanes. Sends ONLY when armed (ENABLED), not dry, and
    lease-held; otherwise scans + logs WOULD-NUDGE and mutates no state. Never recycles anything."""
    lease_ok, lease_reason = _lease_ok()
    send_live = ENABLED and not dry and lease_ok
    lanes = slr.discover_lanes(conn)
    state = _load_state()
    now = time.time()
    print(f"lane-proactive-recycle-nudge — {'LIVE' if send_live else 'SCAN+LOG'} — "
          f"bar={PROACTIVE_PCT}% renudge>=+{RENUDGE_DELTA}%/grace{GRACE_S // 60}m cap={MAX_NUDGES} — "
          f"enabled={ENABLED} lease_ok={lease_ok} ({lease_reason}) dry={dry} — {len(lanes)} lane(s)")

    nudged = 0
    with conn.cursor() as cur:
        for lr in lanes:
            base = lr["base_agent_id"]
            # defence in depth — discover_lanes already excludes singletons; crash loudly if one slips.
            fhb.assert_sre_never_targets_singleton(base)
            session = lr.get("tmux_session")
            tokens, age_s = dm._gauge(cur, base)
            pane_pct, pane_hint_k = dm._pane(session) if session else (None, None)
            known, pct, _last_known, reason = dm.resolve_lane_pct(tokens, age_s, pane_pct, pane_hint_k)
            episode = state.get(base)
            verdict, new_ep = evaluate_proactive_nudge(known, pct, episode, now)
            if new_ep is None:
                state.pop(base, None)
            else:
                state[base] = new_ep
            pct_s = f"{pct}%" if pct is not None else "UNKNOWN"
            print(f"  {base:22s} {pct_s:>7s} -> {verdict}")
            if verdict in ("nudge", "renudge"):
                if not send_live:
                    print(f"            WOULD-NUDGE {lr['lane']} (~{pct_s}, {verdict}) "
                          f"[not sent: enabled={ENABLED} lease_ok={lease_ok} dry={dry}]")
                    continue
                if _nudged_recently(cur, lr["lane"]):
                    print(f"            skip {lr['lane']}: cross-tier dedup (nudged within {COOLDOWN_MIN}m)")
                    continue
                subj, body = proactive_nudge_message(lr["lane"], pct, nudge_n=new_ep["nudges"])
                _send_nudge(cur, conn, base, subj, body)
                print(f"            NUDGED lane '{base}' ({verdict}, ~{pct_s})")
                nudged += 1

    if send_live:  # persist episode timers ONLY when armed + holder (inert/dry never mutates state)
        _save_state(state)
    print(f"done — {nudged} nudged, state {'saved' if send_live else 'unchanged (scan+log)'}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="proactive self-recycle nudge tier (detect + nudge-only; never recycles)")
    ap.add_argument("--dry-run", action="store_true",
                    help="scan + log only; no send, no state write (side-effect-free preview)")
    args = ap.parse_args()

    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        return run(conn, dry=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
