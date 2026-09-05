#!/usr/bin/env python3
"""lane_recycle_deadman.py — the LOUD backstop for the worker-lane self-recycle loop.

WHY (bus 37752 / 37775, op#19141). The recycle loop's job is to keep every lane below the
line. This job's only job is to notice when it FAILS to, and get LOUD — the guard-of-guards
Nazim made a non-negotiable pre-arm: paging must exist BEFORE any auto-fire, and a loop that
goes blind must get louder, not quieter. It PAGES orch-console on two failure modes:

  * SUSTAINED — a lane at >= HARD_PCT for longer than SUSTAIN_S. A real recycle would have
    collapsed it below HARD, so "still >= HARD after the window" IS the "no recycle fired"
    signal; no separate fire-history needed.
  * UNKNOWN — a live lane whose gauge is unreadable/stale (the stale-gauge fail-OPEN: a None
    reading that a naive check treats as not-bloated and SKIPS). Never assume quiet.

It reads context through the ONE canonical source (`context_truth.lane_fire_reading`, gauge-
first, pane a logged cross-check) — the SAME source the executor decides on, so what pages and
what would fire never diverge. It NEVER recycles anything; it only detects and pages. Runs on a
launchd timer. Detect-only is the default; nothing here mutates a lane.

Dead-man for the dead-man: each run stamps a heartbeat row so a stalled backstop is itself
visible (a guard that dies silently is worse than none — CLAUDE.md §2.1).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

# Thresholds. HARD_PCT/SUSTAIN_S are the dead-man's page bar; they are DELIBERATELY above the
# recycler's 80% fire bar — the dead-man only fires when the recycler has already had a chance
# and failed to bring the lane down.
HARD_PCT = int(os.environ.get("DEADMAN_HARD_PCT", "85"))
SUSTAIN_S = int(os.environ.get("DEADMAN_SUSTAIN_S", "1800"))  # 30 min at >= HARD with no drop
# The dead-man's OWN gauge-staleness cutoff — DELIBERATELY well above the executor's 30-min fire
# cutoff and above the gauge's real per-lane refresh. The cost-writer only advances a lane's
# gauge when that lane takes a TURN, so a normally-IDLE lane's gauge ages indefinitely with
# nothing wrong (verified 2026-09-05, Nazim 37788: a 30-min cutoff false-alarmed on every idle
# lane at its refresh boundary). At 60 min a lane must be idle a long while before the gauge is
# even considered "stale", and the pane rescue + last-known floor below handle the rest.
GAUGE_STALE_S = int(os.environ.get("DEADMAN_GAUGE_STALE_S", "3600"))
# On a TRULY-blind lane (gauge stale AND pane blind), blocker-page ONLY when its LAST-KNOWN
# reading was already this elevated — "was ~high, now blind" is the dangerous blind-band case;
# "was low, now idle" is benign (an alive/fresh-heartbeat idle low lane does not bloat). Below
# the floor we still SURFACE it (log_unknown) but do not urgent-page (Nazim 37788).
UNKNOWN_PAGE_FLOOR = int(os.environ.get("DEADMAN_UNKNOWN_FLOOR", "70"))
STATE_PATH = _ORCH_DIR / "state" / "lane_recycle_deadman.json"


def evaluate_deadman(known, pct, first_over_at, now_epoch,
                     hard_pct: int = HARD_PCT, sustain_s: int = SUSTAIN_S,
                     last_known_pct=None, unknown_floor: int = UNKNOWN_PAGE_FLOOR):
    """PURE decision core. Given one lane's current reading and its remembered first-crossing
    epoch, return (verdict, new_first_over_at).

    verdict:
      "ok"             — below the bar / benign-blind; clear any timer.
      "watching"       — at/over the bar but not yet sustained; timer running.
      "page_sustained" — at/over the bar for >= sustain_s; PAGE.
      "page_unknown"   — TRULY blind (gauge stale AND pane blind) AND last-known was elevated
                         (>= unknown_floor); BLOCKER page — a high lane we have lost sight of.
      "log_unknown"    — TRULY blind but last-known was low / never measured; SURFACED in the log
                         (an UNKNOWN is never silent) but NOT urgent-paged — an alive idle low
                         lane does not bloat (Nazim 37788).
    """
    if not known:
        if last_known_pct is not None and last_known_pct >= unknown_floor:
            return "page_unknown", None  # was elevated, now blind — the fail-open Nazim blocked
        return "log_unknown", None       # blind but benign (idle low / never measured) — surface only
    if pct is not None and pct >= hard_pct:
        if first_over_at is None:
            return "watching", now_epoch
        if now_epoch - first_over_at >= sustain_s:
            return "page_sustained", first_over_at
        return "watching", first_over_at
    # comfortably below the bar (a recycle or organic drop) — reset the timer.
    return "ok", None


def resolve_lane_pct(gauge_tokens, gauge_age_s, pane_pct, pane_hint_k,
                     window: int = 1_000_000, max_gauge_age_s: int = GAUGE_STALE_S):
    """Best available reading for the dead-man's 'can I see this lane, and how full' question.
    Returns (known, pct, last_known_pct, reason).

    GAUGE-FIRST (aligned with the executor's fire decision — a FRESH gauge decides and sees the
    pane's blind band). Only when the gauge is stale/unreadable does it fall back to the PANE as
    a measurement (this is what rescues an idle lane whose gauge froze from a false UNKNOWN). A
    lane is TRULY unknown only when BOTH signals fail; last_known_pct carries the stale gauge's
    pct so the caller can tell was-high (dangerous) from was-low (benign).
    """
    from scripts.lib import context_truth as ct  # repo root is on sys.path via the bootstrap
    lfr = ct.lane_fire_reading(gauge_tokens=gauge_tokens, gauge_age_s=gauge_age_s,
                               window=window, max_gauge_age_s=max_gauge_age_s)
    last_known = ct._pct_from_tokens(gauge_tokens, window)
    if lfr.known:
        return True, lfr.pct, last_known, lfr.reason
    # gauge unusable — can the pane measure it? (pane-only resolve, no gauge)
    pane = ct.resolve(pane_pct=pane_pct, pane_hint_k=pane_hint_k, gauge_tokens=None, window=window)
    if pane.known:
        return True, pane.pct, last_known, (f"gauge stale ({gauge_age_s}s); pane {pane.source}="
                                            f"{pane.pct}% (fallback measurement)")
    lk = f"{last_known}%" if last_known is not None else "never measured"
    return False, None, last_known, (f"UNKNOWN — gauge stale ({gauge_age_s}s) AND pane blind; "
                                     f"last-known {lk}")


# ── DB / state / paging shell (never recycles; only detects + pages) ──────────

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


def _gauge(cur, base: str):
    """(tokens, age_s) of the freshest gauge row for this base, or (None, None). NO freshness
    filter here — lane_fire_reading applies the staleness cutoff so a stale row becomes UNKNOWN
    (with its age) rather than vanishing into a None the caller might read as quiet."""
    cur.execute(
        "SELECT latest_context_tokens, "
        "       extract(epoch FROM (now() - COALESCE(ended_at, created_at)))::int AS age_s "
        "FROM cc_session_costs "
        "WHERE cc_identity = %s AND latest_context_tokens IS NOT NULL "
        "ORDER BY COALESCE(ended_at, created_at) DESC LIMIT 1",
        [base])
    row = cur.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, None)


def _pane(session: str):
    """(pane_pct, pane_hint_k) best-effort — a logged cross-check only, never the decider. A
    tmux/capture failure must not break the backstop, so swallow to (None, None)."""
    try:
        from lib import pane_bloat_signal as pbs
        return pbs.pane_context_pct(session), pbs.pane_bloat_k(session)
    except Exception:
        return None, None


def _paged_today(cur, lane: str, verdict: str) -> bool:
    cur.execute(
        "SELECT 1 FROM agent_messages "
        "WHERE from_agent='cc-fleet-health' AND to_agent='orch-console' "
        "  AND subject LIKE %s AND created_at >= date_trunc('day', now()) LIMIT 1",
        [f"[dead-man] {lane}: {verdict}%"])
    return cur.fetchone() is not None


def page_message(lane: str, verdict: str, reason: str,
                 hard_pct: int = HARD_PCT, sustain_s: int = SUSTAIN_S):
    """PURE (subject, body) for a dead-man page. The subject's `[dead-man] {lane}: {verdict}`
    prefix is the dedup anchor (`_paged_today` LIKEs it) — keep it STABLE. ELI5 what/why/what-
    to-do per fleet doctrine."""
    subject = (f"[dead-man] {lane}: {verdict} — self-recycle loop is NOT keeping this lane "
               f"below {hard_pct}%")
    if verdict == "page_sustained":
        what = (f"Lane '{lane}' has been at/over {hard_pct}% context for more than "
                f"{sustain_s // 60} min and nothing has recycled it. A working recycle would "
                f"have dropped it below {hard_pct}% by now, so the loop is failing on this lane.")
    else:  # page_unknown
        what = (f"Lane '{lane}' is LIVE but its context gauge is unreadable/stale — I cannot "
                f"measure how full it is, so I will not assume it is fine (this is the "
                f"stale-gauge fail-open). It may be silently bloated.")
    body = (f"TL;DR: the self-recycle loop needs a human look at lane '{lane}'.\n\n"
            f"WHAT: {what}\n"
            f"EVIDENCE: {reason}\n"
            f"WHAT TO DO: verify the lane (pane + gauge), and recycle it by hand if it is "
            f"genuinely bloated; if the gauge is wrong/stale, check its cost-writer. This is a "
            f"DETECT-ONLY backstop — I did not touch the lane.")
    return subject, body


def _page(cur, conn, lane: str, verdict: str, reason: str, dry: bool) -> str:
    subject, body = page_message(lane, verdict, reason)
    if dry:
        print(f"            WOULD PAGE orch-console: {verdict}")
        return "would-page"
    if _paged_today(cur, lane, verdict):
        print(f"            already paged {lane}/{verdict} today — deduped")
        return "deduped"
    cur.execute(
        "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
        " priority, requires_response, created_at) "
        "VALUES ('cc-fleet-health','orch-console','blocker',%s,%s,'P1',false, now())",
        [subject, body])
    conn.commit()
    print(f"            PAGED orch-console: {verdict}")
    return "paged"


def _stamp_heartbeat(state: dict, now: float) -> None:
    """Guard-of-guards: record this backstop's own last run so a STALLED dead-man is itself
    visible. A guard that dies silently is worse than none (CLAUDE.md §2.1)."""
    state["_heartbeat_epoch"] = now


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="self-recycle dead-man (detect + page only; never recycles)")
    ap.add_argument("--dry-run", action="store_true",
                    help="detect + log only; no page, no state write (side-effect-free preview)")
    args = ap.parse_args()

    import time
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2

    from scripts.sre_lane_recycle import discover_lanes

    now = time.time()
    conn = psycopg.connect(dsn)
    lanes = discover_lanes(conn)
    state = _load_state()
    print(f"lane-recycle dead-man — {'DRY-RUN' if args.dry_run else 'LIVE'} — "
          f"bar={HARD_PCT}% sustained>{SUSTAIN_S // 60}m — {len(lanes)} lane(s)")

    paged = 0
    with conn.cursor() as cur:
        for lr in lanes:
            base = lr["base_agent_id"]
            session = lr.get("tmux_session")
            tokens, age_s = _gauge(cur, base)
            pane_pct, pane_hint_k = _pane(session) if session else (None, None)
            known, pct, last_known, reason = resolve_lane_pct(tokens, age_s, pane_pct, pane_hint_k)
            first_over = state.get(base)
            verdict, new_first = evaluate_deadman(known, pct, first_over, now,
                                                  last_known_pct=last_known)
            if new_first is None:
                state.pop(base, None)
            else:
                state[base] = new_first
            pct_s = f"{pct}%" if pct is not None else "UNKNOWN"
            print(f"  {base:22s} {pct_s:>7s} -> {verdict}")
            if verdict == "log_unknown":
                # An UNKNOWN is never silent (CLAUDE.md verify-not-assert): surface it in the log,
                # but it is not urgent (alive idle low lane) so it does not blocker-page.
                print(f"            LOG (surfaced, not paged): {reason}")
            elif verdict in ("page_sustained", "page_unknown"):
                if _page(cur, conn, lr["lane"], verdict, reason, args.dry_run) == "paged":
                    paged += 1

    if not args.dry_run:
        _stamp_heartbeat(state, now)
        _save_state(state)
    print(f"done — {paged} paged, state {'unchanged (dry-run)' if args.dry_run else 'saved'}.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
