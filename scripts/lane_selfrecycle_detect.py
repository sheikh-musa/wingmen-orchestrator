#!/usr/bin/env python3
"""lane_selfrecycle_detect.py — STAGE-0 DETECT-ONLY auto-recycle detector.

The FIRST rung of the staged-arm ladder (Stage-0 detect -> Stage-1 supervised ->
Stage-2 armed) for SRE worker-lane auto-recycle. This script FIRES NOTHING: it
enumerates live worker lanes, evaluates the recycle gates for each, and PRINTS a
per-lane verdict ("WOULD-RECYCLE ..." / "skip ...: <reason>"). It NEVER runs a
/clear, a reset, a nudge, a send-keys, or ANY state-changing action, and it never
writes to the substrate. Read-only, log-only.

WHY IT EXISTS — it fixes two root-cause bugs in scripts/sre_lane_recycle.py that
let a genuinely-bloated idle worker lane (cc-irsyad-1, tmux 'irsyad', 97% context,
idle) slip through undetected:

  GAP-1  STALE-GAUGE BLIND SPOT. sre_lane_recycle.gate_context_bloat reads the
     cc_session_costs DB gauge with a 30-min freshness cutoff; when telemetry is
     stale it returns "cannot measure" -> False -> the bloated lane is never
     flagged. cc-irsyad-1's gauge was ~41h stale while its PANE plainly showed the
     live context level. FIX: derive the context % from the LIVE TMUX PANE
     (parse_pane_context_pct), not the gauge. The gauge is still read, but ONLY to
     LOG a stale/disagreeing-substrate signal — never as the bloat source of truth.

  GAP-2  SESSION TARGETING. sre_lane_recycle enumerates DISTINCT ON (base_agent_id)
     from agent_status, so a shared base (cc-irsyad has 4 live sessions:
     irsyad / irsyad-coord / irsyad-prog1 / irsyad-prog2) collapses to ONE row and
     the specific bloated instance is invisible + un-targetable. FIX: enumerate
     PER LIVE SESSION (enumerate_worker_sessions) so every worker instance is
     evaluated and named by its exact tmux session.

Everything else REUSES sre_lane_recycle + fleet_health_boundaries + the
composer_capture.sh ghost-vs-draft discriminator, unchanged.

Boundary: worker lanes ONLY. Singletons (hub / cai / Nazim / the SRE itself) are
excluded at enumeration AND re-asserted via fleet_health_boundaries — defence in
depth. Nothing here can target a singleton, and nothing here acts.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
_LIB = _ORCH_DIR / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
sys.path.insert(0, str(_ORCH_DIR / "scripts"))

import fleet_health_boundaries as fhb  # noqa: E402
import sre_lane_recycle as slr  # noqa: E402  (REUSE its gate helpers, read-only)

# ── config (module constants, env-overridable) ───────────────────────────────
TMUX_BIN = os.environ.get("SRE_TMUX_BIN", "/usr/local/bin/tmux")
COMPOSER_LIB = str(_LIB / "composer_capture.sh")
# GAP-1 bloat threshold on the LIVE-PANE context %. Operator "auto-recycle at 90%".
BLOAT_PCT = float(os.environ.get("CTX_WD_LANE_BLOAT_PCT", "90"))
# Token-count -> % conversion window (worker lanes default to the 1M context beta).
# Only used when the pane shows a token count rather than an explicit percentage.
CTX_WINDOW = int(os.environ.get("CONSOLE_CTX_WINDOW", "1000000"))
# Sustained-idle: two pane captures this many seconds apart must be byte-identical.
IDLE_SETTLE_S = float(os.environ.get("SRE_IDLE_SETTLE_S", "4.5"))
# How many trailing non-blank lines of the pane count as the "status region" where
# CC renders its live context chrome (mirrors composer_capture's tail -12 anchor).
STATUS_TAIL = int(os.environ.get("SRE_STATUS_TAIL", "15"))
_TMUX_TIMEOUT = 6

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ── GAP-1: LIVE-PANE context % (never the stale gauge) ───────────────────────
def parse_pane_context_pct(pane_text: "str | None", window: int = CTX_WINDOW) -> "float | None":
    """Derive the lane's live context % from its tmux pane. The whole GAP-1 fix.

    Handles both forms Claude-Code renders on/near its status line:
      * an explicit percentage — "NN% context used" (authoritative, no window
        needed) or "NN% context left" (used = 100 - left);
      * a token count — "/clear to save NNN[.N]k tokens" (or "NNNk tokens"), which
        is converted to a % via `window` (default 1M).
    Only the last STATUS_TAIL non-blank lines are scanned (the status region), so
    a transcript that merely MENTIONS "% context used" up-scroll can't false-trip.
    Returns the % (0..100) or None. None == no readable live context signal: CC
    only shows the chrome past a display threshold, so an absent line means the
    lane is well under threshold -> fail-safe, NOT a candidate. An unreadable pane
    (None text) is likewise None (cannot confirm bloat -> never flag)."""
    if not pane_text:
        return None
    txt = _ANSI_RE.sub("", pane_text)
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    if not lines:
        return None
    tail = lines[-STATUS_TAIL:]
    # 1) explicit percentage wins (no window assumption).
    for ln in reversed(tail):
        m = re.search(r"(\d{1,3})\s*%\s*context\s+used", ln, re.I)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d{1,3})\s*%\s*context\s+left", ln, re.I)
        if m:
            return max(0.0, 100.0 - float(m.group(1)))
    # 2) token count -> % via window.
    for ln in reversed(tail):
        m = re.search(r"(?:save\s+)?([\d,]+(?:\.\d+)?)\s*([kKmM])\s+tokens", ln)
        if m:
            num = float(m.group(1).replace(",", ""))
            mult = 1000 if m.group(2).lower() == "k" else 1_000_000
            return round(num * mult / float(window) * 100.0, 1)
    return None


def gate_bloat(pane_pct: "float | None", threshold: float = BLOAT_PCT) -> bool:
    """True iff the LIVE-PANE context % is known AND >= threshold. Fail-safe:
    None (no readable reading) -> False (never flag a lane we can't confirm)."""
    return pane_pct is not None and pane_pct >= threshold


# ── pane capture seams (module-level so tests can monkeypatch them) ───────────
def _capture_raw(session: str) -> "str | None":
    """Plain pane text (no SGR) — for the context parse + the idle byte-diff."""
    try:
        r = subprocess.run([TMUX_BIN, "capture-pane", "-t", f"={session}:0.0", "-p", "-S", "-40"],
                           capture_output=True, text=True, timeout=_TMUX_TIMEOUT)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _pane_text_busy(pane_text: "str | None") -> bool:
    """True iff an active-turn marker is present. Delegates to composer_capture.sh
    `_cc_text_busy` (the fleet's single definition of 'busy': 'esc to interrupt',
    'Waiting for N background agents', or a thinking/tokens spinner) — REUSE, not a
    reimplementation. Any error -> True (fail-closed: unknown counts as busy)."""
    if not pane_text:
        return False
    try:
        script = f'source "{COMPOSER_LIB}"; _cc_text_busy "$1" && echo BUSY || echo IDLE'
        r = subprocess.run(["bash", "-c", script, "_", pane_text],
                           capture_output=True, text=True, timeout=_TMUX_TIMEOUT)
        return r.stdout.strip().endswith("BUSY")
    except Exception:
        return True


def gate_idle_sustained(session: str, settle_s: float = IDLE_SETTLE_S) -> "tuple[bool, str]":
    """Idle part (a): sustained idle. Capture the pane twice ~settle_s apart; idle
    iff BOTH captures succeed, are BYTE-IDENTICAL (nothing animating/scrolling), and
    carry no active-turn marker. Fail-closed: an unreadable pane, a changed pane, or
    any busy marker -> NOT idle. Returns (idle, reason)."""
    raw1 = _capture_raw(session)
    if raw1 is None:
        return False, "pane unreadable (fail-closed)"
    time.sleep(settle_s)
    raw2 = _capture_raw(session)
    if raw2 is None:
        return False, "pane unreadable on re-capture (fail-closed)"
    if raw1 != raw2:
        return False, "pane changed across settle window (animating/active)"
    if _pane_text_busy(raw2):
        return False, "active-turn marker present (esc-to-interrupt / bg-agents / thinking)"
    return True, "sustained-idle (byte-identical, no active-turn marker)"


# ── idle part (b): composer clean (REUSE composer_capture.sh ghost-vs-draft) ──
def probe_composer(session: str) -> "dict | None":
    """Run composer_capture.sh against the lane and return its verdict:
    {empty, ghost, partial, basis, flat}. This REUSES the codebase's exact
    ghost-vs-real-draft discriminator — a DIM history ghost (CC_GHOST=1) vs a real
    (bright OR dim-but-novel) staged draft. Returns None on probe failure."""
    try:
        script = (
            f'source "{COMPOSER_LIB}"; '
            'composer_parse_pane "$1" "$2"; '
            'printf "EMPTY=%s\\nGHOST=%s\\nPARTIAL=%s\\nBASIS=%s\\nFLAT=%s\\n" '
            '"${CC_EMPTY:-?}" "${CC_GHOST:-?}" "${CC_PARTIAL:-?}" "${CC_PH_BASIS:-?}" "${CC_FLAT:-}"'
        )
        r = subprocess.run(["bash", "-c", script, "_", TMUX_BIN, f"={session}:0.0"],
                           capture_output=True, text=True, timeout=_TMUX_TIMEOUT)
        if r.returncode != 0:
            return None
        out: dict = {}
        for ln in r.stdout.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k.lower()] = v
        if "empty" not in out:
            return None
        return {
            "empty": out.get("empty") == "1",
            "ghost": out.get("ghost") == "1",
            "partial": out.get("partial", "?"),
            "basis": out.get("basis", "?"),
            "flat": out.get("flat", ""),
        }
    except Exception:
        return None


def gate_composer_clean(probe: "dict | None") -> "tuple[bool, str]":
    """Idle part (b): the composer holds NO REAL staged draft. Clean iff the
    composer is EMPTY, or holds only a DIM history-ghost (CC_GHOST=1, empty
    underneath). A real staged draft (bright, or dim-but-novel) is a genuine
    unsubmitted next step -> NOT clean -> HOLD (losslessness; a later stage would
    ABORT+escalate, never auto-clear). Fail-closed: unreadable / unreliable
    ('noprompt') capture -> NOT clean. Returns (clean, reason)."""
    if probe is None:
        return False, "composer unreadable (fail-closed)"
    if probe.get("partial") == "noprompt":
        return False, "composer capture unreliable (noprompt, fail-closed)"
    if probe.get("empty"):
        return True, "composer empty"
    if probe.get("ghost"):
        return True, "composer holds only a dim history-ghost (empty underneath)"
    flat = (probe.get("flat") or "").strip()
    snippet = flat[:60] + ("…" if len(flat) > 60 else "")
    return False, f"staged draft present (real-per-content, NOT probe-verified — may be a re-rendered/scrolled-off ghost) -> HOLD: {snippet!r}"


# ── checkpointable (Stage-0: REPORT only) ────────────────────────────────────
def handoff_state(conn, base_agent_id: str, notes: "str | None") -> str:
    """REPORT whether a fresh handoff exists (would a /clear be lossless?). Reuses
    sre_lane_recycle._newest_handoff_mtime + last_material_action_epoch. Returns
    'fresh' | 'stale' | 'missing'. Fail-closed toward conservative: if the last
    material action can't be established, the handoff can't be PROVEN current ->
    'stale' (never claim fresh on an unverifiable basis)."""
    ho = slr._newest_handoff_mtime(base_agent_id, notes)
    if ho is None:
        return "missing"
    last = slr.last_material_action_epoch(conn, base_agent_id)
    if last is None:
        return "stale"
    return "fresh" if ho >= last else "stale"


def git_state(worktree_path: "str | None") -> str:
    """REPORT the lane worktree's git cleanliness. Reuses sre_lane_recycle.
    gate_git_clean for the clean verdict; distinguishes 'unknown' (no path / not a
    dir / git error) from 'dirty'. Returns 'clean' | 'dirty' | 'unknown'."""
    if slr.gate_git_clean(worktree_path):
        return "clean"
    if not worktree_path or not os.path.isdir(os.path.expanduser(worktree_path)):
        return "unknown"
    return "dirty"


# ── GAP-2: per-session enumeration (NOT distinct-on-base) ────────────────────
def _fetch_status_rows(conn) -> "list[dict]":
    """DB seam (monkeypatched in tests). Every LIVE worker session — one row PER
    tmux session (agent_id) with a fresh (<30min) heartbeat — NOT collapsed to one
    per base. Singletons (hub / cai / Nazim / SRE, from fhb.SINGLETON_BODIES) are
    excluded here; the boundary re-asserts it downstream (defence in depth)."""
    singletons = sorted(fhb.SINGLETON_BODIES)
    ph = ",".join(["%s"] * len(singletons))
    q = (
        "SELECT s.agent_id, s.base_agent_id, s.tmux_session, s.host, "
        "       round(extract(epoch from (now()-s.last_heartbeat))/60)::int AS hb_min, "
        "       l.worktree_path, l.notes "
        "FROM agent_status s "
        "LEFT JOIN LATERAL (SELECT worktree_path, notes FROM fleet_lanes fl "
        "                   WHERE fl.base_agent_id = s.base_agent_id "
        "                   ORDER BY (worktree_path IS NOT NULL) DESC, lane LIMIT 1) l ON true "
        "WHERE s.tmux_session IS NOT NULL AND s.base_agent_id IS NOT NULL "
        f"  AND s.base_agent_id NOT IN ({ph}) "
        "  AND s.last_heartbeat > now() - interval '30 minutes' "
        "ORDER BY s.base_agent_id, s.tmux_session"
    )
    with conn.cursor() as cur:
        cur.execute(q, singletons)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def enumerate_worker_sessions(conn) -> "list[dict]":
    """GAP-2 fix. Return every live worker SESSION as its own evaluable unit
    (keeps all N sessions of a shared base distinct + individually targetable).
    Belt-and-braces: any singleton that somehow appears is dropped AND would be
    refused by fhb.assert_sre_never_targets_singleton."""
    out: list[dict] = []
    for r in _fetch_status_rows(conn):
        base = r.get("base_agent_id")
        if base in fhb.SINGLETON_BODIES:      # defence in depth — never target a singleton
            continue
        out.append(r)
    return out


def gauge_reading(conn, base_agent_id: str, session: str) -> "tuple[float, float] | None":
    """READ-ONLY substrate cross-check for the STALE-GAUGE signal (GAP-1 logging).
    Latest cc_session_costs context tokens for this instance, ANY age (no freshness
    filter — the whole point is to surface staleness). Prefers a per-session
    (sub_tag) row, else the base. Returns (pct_of_window, age_minutes) or None."""
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT latest_context_tokens, extract(epoch from (now()-created_at))/60 "
                "FROM cc_session_costs "
                "WHERE cc_identity=%s AND latest_context_tokens IS NOT NULL "
                "ORDER BY (sub_tag=%s) DESC, created_at DESC LIMIT 1",
                [base_agent_id, session])
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        return round(int(row[0]) / float(CTX_WINDOW) * 100.0, 1), round(float(row[1]), 0)
    except Exception:
        return None


# ── per-lane evaluation (pure verdict; FIRES NOTHING) ────────────────────────
def evaluate_session(conn, lane: dict) -> dict:
    """Evaluate ALL gates for one live worker session and return a verdict dict.
    Read-only. Never acts. The verdict:
      WOULD-RECYCLE                 — bloated + idle + clean + git-clean + fresh handoff
      WOULD-RECYCLE-PENDING-CHKPT   — bloated + idle + clean + git-clean, handoff
                                      stale/missing (a later stage checkpoints first;
                                      surfaced so a bloated-idle lane is never hidden)
      skip: <reason>                — first failing gate
    """
    session = lane.get("tmux_session")
    base = lane.get("base_agent_id")
    v = {"session": session, "base": base, "agent_id": lane.get("agent_id"),
         "host": lane.get("host"), "hb_min": lane.get("hb_min"),
         "pane_pct": None, "gauge_pct": None, "gauge_age_min": None,
         "gauge_stale": False, "gauge_disagree": False,
         "idle": None, "idle_reason": "", "composer_clean": None, "composer_reason": "",
         "handoff": "?", "git": "?", "verdict": "skip", "reason": ""}

    # Boundary belt: this must NEVER be a singleton (enumeration already excludes
    # them; assert regardless so a mis-wire crashes loudly rather than acting).
    fhb.assert_sre_never_targets_singleton(base, identity=fhb.SRE_AGENT_ID)

    # GAP-1: live-pane context %, plus the read-only gauge cross-check.
    raw = _capture_raw(session)
    pane_pct = parse_pane_context_pct(raw)
    v["pane_pct"] = pane_pct
    g = gauge_reading(conn, base, session)
    if g is not None:
        v["gauge_pct"], v["gauge_age_min"] = g
        v["gauge_stale"] = g[1] is not None and g[1] > slr._CTX_FRESH_MIN
        if pane_pct is not None and abs(g[0] - pane_pct) >= 10:
            v["gauge_disagree"] = True

    if not gate_bloat(pane_pct):
        v["reason"] = (f"ctx {pane_pct}% < {BLOAT_PCT:.0f}% threshold" if pane_pct is not None
                       else "no live context reading (below display threshold / pane unreadable)")
        return v

    # bloated -> evaluate the idle floor (sustained-idle + composer-clean).
    idle, idle_reason = gate_idle_sustained(session)
    v["idle"], v["idle_reason"] = idle, idle_reason
    probe = probe_composer(session)
    clean, comp_reason = gate_composer_clean(probe)
    v["composer_clean"], v["composer_reason"] = clean, comp_reason
    v["handoff"] = handoff_state(conn, base, lane.get("notes"))
    v["git"] = git_state(lane.get("worktree_path"))

    if not idle:
        v["reason"] = f"not idle: {idle_reason}"
        return v
    if not clean:
        v["reason"] = comp_reason
        return v
    if v["git"] != "clean":
        v["reason"] = f"git {v['git']} (uncommitted work or unknown worktree)"
        return v
    if v["handoff"] != "fresh":
        v["verdict"] = "WOULD-RECYCLE-PENDING-CHKPT"
        v["reason"] = f"handoff {v['handoff']} — Stage-1+ would checkpoint first, then recycle"
        return v
    v["verdict"] = "WOULD-RECYCLE"
    v["reason"] = f"ctx={pane_pct}% idle+clean+git-clean+fresh-handoff"
    return v


def _fmt_row(v: dict) -> str:
    pane = f"{v['pane_pct']:.0f}%" if v["pane_pct"] is not None else "  ?"
    gauge = f"{v['gauge_pct']:.0f}%" if v["gauge_pct"] is not None else "  ?"
    gflag = ""
    if v["gauge_stale"]:
        gflag += " STALE"
    if v["gauge_disagree"]:
        gflag += " DISAGREE"
    idle = "-" if v["idle"] is None else ("y" if v["idle"] else "n")
    comp = "-" if v["composer_clean"] is None else ("clean" if v["composer_clean"] else "draft")
    return (f"  [{v['verdict']:>26s}] {v['session']:<16s} base={v['base']:<18s} "
            f"pane={pane:>4s} gauge={gauge:>4s}{gflag} "
            f"idle={idle} composer={comp} handoff={v['handoff']} git={v['git']}\n"
            f"        reason: {v['reason']}")


def run(conn) -> int:
    lanes = enumerate_worker_sessions(conn)
    print(f"lane_selfrecycle_detect — STAGE-0 DETECT-ONLY (fires nothing) — "
          f"{len(lanes)} live worker session(s) — bloat>={BLOAT_PCT:.0f}% live-pane — "
          f"{datetime.now(timezone.utc).isoformat()}")
    would = pending = 0
    for lane in sorted(lanes, key=lambda r: (r.get("base_agent_id") or "", r.get("tmux_session") or "")):
        v = evaluate_session(conn, lane)
        print(_fmt_row(v))
        if v["verdict"] == "WOULD-RECYCLE":
            would += 1
        elif v["verdict"] == "WOULD-RECYCLE-PENDING-CHKPT":
            pending += 1
    print(f"\nsummary: {would} WOULD-RECYCLE, {pending} WOULD-RECYCLE-PENDING-CHECKPOINT, "
          f"{len(lanes) - would - pending} skipped. DETECT-ONLY — nothing was touched.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage-0 DETECT-ONLY worker-lane auto-recycle detector (fires nothing).")
    ap.add_argument("--self-test", action="store_true", help="offline gate self-test + exit")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        return run(conn)
    finally:
        conn.close()


def _self_test() -> int:
    """Offline proof of the fail-closed gate logic (no DB, no tmux)."""
    fails = []

    def ok(c, m):
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")
        if not c:
            fails.append(m)

    ok(parse_pane_context_pct("✻  97% context used") == 97.0, "pane parse: '97% context used' -> 97")
    ok(parse_pane_context_pct("12% context left") == 88.0, "pane parse: '12% context left' -> 88")
    ok(parse_pane_context_pct("new task? /clear to save 858.1k tokens") == 85.8,
       "pane parse: '858.1k tokens' @1M -> 85.8")
    ok(parse_pane_context_pct("nothing here") is None, "pane parse: no line -> None")
    ok(gate_bloat(97.0) is True and gate_bloat(85.0) is False and gate_bloat(None) is False,
       "gate_bloat: 97 True / 85 False / None False")
    ok(gate_composer_clean(None)[0] is False, "composer: unreadable fail-closed")
    ok(gate_composer_clean({"empty": True, "partial": "ok"})[0] is True, "composer: empty clean")
    ok(gate_composer_clean({"empty": False, "ghost": True, "partial": "ok"})[0] is True,
       "composer: dim ghost clean")
    ok(gate_composer_clean({"empty": False, "ghost": False, "partial": "ok", "flat": "poll bus"})[0] is False,
       "composer: real draft NOT clean")
    print()
    print("LANE_SELFRECYCLE_DETECT SELF-TEST " + ("FAILED" if fails else "PASSED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
