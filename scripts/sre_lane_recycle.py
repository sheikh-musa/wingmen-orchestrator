#!/usr/bin/env python3
"""sre_lane_recycle.py — SRE-driven auto-recycle of WEDGED/bloated WORKER LANES.

The detect+checkpoint+recycle-lanes upgrade (operator op#8995, op#9141 GO,
cai CAI-RESP-681 co-sign). Lets cc-fleet-health recycle a worker lane in place
(checkpoint -> /clear -> reboot from handoff) instead of only paging — but ONLY
under the hard, mechanically-verified gates cai bound:

  cond 3 (HARD): WORKER LANES ONLY. cai / the hub / Nazim are structurally
     unreachable — enforced in fleet_health_boundaries.assert_sre_never_targets_
     singleton, called first, every time.
  cond 1/2: idle + git-clean + FRESH handoff, ALL mechanically verified True at
     the moment of action. fresh_handoff is load-bearing (makes the reset
     lossless). Any gate not confirmable -> False -> NO reset (don't assume).
  cond 4: an attributable audit row is written BEFORE the /clear (actor + reason
     + gates-verified), fail-closed-visible.
  cond 5: DISARMED until an explicit post-demo arm (CTX_WD_LANE_ARM=1 / --arm).
     Default is DETECT-ONLY: compute gates + report what it WOULD do, touch
     nothing.

This module NEVER touches a singleton — that stays the hub's CAI-500 path
(context_health_watchdog.py --arm=red). It reuses reset_lane.sh for the actual
in-place /clear+boot (which itself refuses a busy lane — defence in depth on the
idle gate).
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ORCH_DIR / "scripts" / "lib"))
import fleet_health_boundaries as fhb  # noqa: E402
import handoff_compaction_policy as hcp  # noqa: E402  (SSOT staged handoff compaction)

TMUX = os.environ.get("SRE_TMUX_BIN", "/usr/local/bin/tmux")
_TMUX_TIMEOUT = 5

# Spinner glyphs Claude-Code shows on its ACTIVE status line (mirrors panes.py).
_SPINNER_GLYPHS = "✻✳✶✷✸✹✺✽❋⚹"

# ── context-bloat trigger (the operator's "auto-recycle AT 80%") ──────────────
# A 4th requirement layered ON TOP of the CAI-681 safety floor (idle+git_clean+
# fresh_handoff, enforced unchanged by fleet_health_boundaries): only recycle a
# lane that is actually bloated, never a low-context idle one (a reset is lossless
# but not free). Window model MIRRORS context_health_watchdog: a transient worker
# lane's window defaults to CONSOLE_CTX_WINDOW (1M); a reading > window means
# "cannot measure" -> fail-closed (never recycle on a number we can't trust), the
# same honesty the watchdog uses. NOTE (flagged to the SRE, Gap-1): the real
# per-lane window (1M vs 200K) changes the % materially — once the SRE exposes a
# canonical per-lane pct, switch gate_context_bloat to consume THAT (single source).
_CTX_WINDOW = int(os.environ.get("CONSOLE_CTX_WINDOW", "1000000"))
_CTX_HARD = float(os.environ.get("CTX_WD_HARD", "0.80"))
_CTX_FRESH_MIN = 30  # minutes: telemetry older than this can't prove current bloat


# ── gate computation — mechanical, fail-closed ────────────────────────────────
def _raw_pane(session: str) -> "str | None":
    try:
        r = subprocess.run([TMUX, "capture-pane", "-t", f"={session}:0.0", "-p", "-S", "-200"],
                           capture_output=True, text=True, timeout=_TMUX_TIMEOUT)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def gate_idle(session: str) -> bool:
    """True iff the lane's pane is present AND not actively generating a turn.
    Fail-closed: an unreadable pane (session gone, capture failed) is NOT idle."""
    raw = _raw_pane(session)
    if raw is None:
        return False
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return False
    # 'esc to interrupt' shows ONLY while a turn runs -> working, not idle.
    if any("esc to interrupt" in ln.lower() for ln in lines):
        return False
    # An ACTIVE spinner (running verb ellipsis / live token counter) -> working.
    spinner = next((ln for ln in reversed(lines) if ln.lstrip()[:1] in _SPINNER_GLYPHS), None)
    if spinner and ("…" in spinner or "↑" in spinner or "↓" in spinner or "thinking" in spinner.lower()):
        return False
    return True


def gate_git_clean(worktree_path: "str | None") -> bool:
    """True iff the lane's worktree has NO uncommitted changes. Fail-closed: a
    missing/unreadable worktree, or any git error, is NOT clean (a reset there
    could discard uncommitted work)."""
    if not worktree_path:
        return False
    wt = os.path.expanduser(worktree_path)
    if not os.path.isdir(wt):
        return False
    try:
        r = subprocess.run(["git", "-C", wt, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=_TMUX_TIMEOUT)
        if r.returncode != 0:
            return False
        return r.stdout.strip() == ""
    except Exception:
        return False


def _newest_handoff_path(base_agent_id: str, notes: "str | None") -> "str | None":
    """ABSOLUTE path of the newest matching handoff file, or None. Glob is
    reports/<base>-handoff-*.md by default; a lane may override via a
    'handoff_glob=...' token in fleet_lanes.notes. Returned absolute so the boot
    string can name an EXACT file — a freshly-cleared lane's cwd is its project
    worktree, so a relative 'reports/' would resolve to the wrong tree (op#14539 fix 1)."""
    pattern = None
    if notes:
        for tok in notes.split():
            if tok.startswith("handoff_glob="):
                pattern = tok.split("=", 1)[1]
                break
    if not pattern:
        short = base_agent_id[3:] if base_agent_id.startswith("cc-") else base_agent_id
        pattern = f"reports/{short}-handoff-*.md"
    files = glob.glob(str(_ORCH_DIR / pattern))
    if not files:
        return None
    try:
        newest = max(files, key=os.path.getmtime)
    except Exception:
        return None
    return os.path.abspath(newest)


def _newest_handoff_mtime(base_agent_id: str, notes: "str | None") -> "float | None":
    """mtime (epoch) of the newest handoff file for this lane, or None."""
    p = _newest_handoff_path(base_agent_id, notes)
    if p is None:
        return None
    try:
        return os.path.getmtime(p)
    except Exception:
        return None


def gate_fresh_handoff(base_agent_id: str, notes: "str | None", last_action_epoch: "float | None") -> bool:
    """cond 2, LOAD-BEARING. True iff a handoff exists AND was written at/after
    the lane's last material action — so a /clear reconstitutes losslessly.
    Fail-closed: no handoff, or an indeterminable last action, or a handoff older
    than the last action -> False (a stale handoff means the reset loses work)."""
    ho = _newest_handoff_mtime(base_agent_id, notes)
    if ho is None:
        return False
    if last_action_epoch is None:      # can't establish 'last material action' -> don't assume
        return False
    return ho >= last_action_epoch


def last_material_action_epoch(conn, base_agent_id: str) -> "float | None":
    """The lane's last material action, mechanically: the timestamp of its most
    recent bus message (agent_messages.from_agent = base). None if it never
    posted (then fresh_handoff fails closed — we can't prove the handoff is
    current)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(created_at) FROM agent_messages WHERE from_agent = %s", [base_agent_id])
            row = cur.fetchone()
        ts = row[0] if row else None
        return ts.timestamp() if ts is not None else None
    except Exception:
        return None


def latest_context_frac(conn, base_agent_id: str, window: int = _CTX_WINDOW) -> "float | None":
    """Latest context fraction (0..1) for a lane from cc_session_costs, or None when
    it CANNOT be established: no connection, no fresh reading (< _CTX_FRESH_MIN old),
    a non-positive reading, or a reading that exceeds the assumed window (the
    'cannot measure' honesty from context_health_watchdog — a wrong window must
    read as unknown, never as a confident %). None always fails the bloat gate."""
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            # op#14565: key freshness on the LAST-WRITE time, not the frozen
            # session-start created_at. The auto-writer UPSERTs a long-lived lane's
            # row and advances ended_at (= session-file mtime) every cycle while
            # created_at stays pinned at session start. Filtering on created_at
            # wrongly excluded a bloated-but-un-recycled lane (created_at hours old,
            # telemetry fresh) -> None -> gate never fired. COALESCE(ended_at,
            # created_at) matches what context_health_watchdog already does and
            # falls back to created_at for rows a non-auto-writer source left
            # ended_at NULL on. Fail-closed is preserved: no row inside the window
            # still -> None.
            cur.execute(
                "SELECT latest_context_tokens FROM cc_session_costs "
                "WHERE cc_identity = %s AND latest_context_tokens IS NOT NULL "
                f"  AND COALESCE(ended_at, created_at) > now() - interval '{_CTX_FRESH_MIN} minutes' "
                "ORDER BY COALESCE(ended_at, created_at) DESC LIMIT 1",
                [base_agent_id])
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        ctx = int(row[0])
        if ctx <= 0 or ctx > window:      # non-positive / over-window -> unmeasurable
            return None
        return ctx / float(window)
    except Exception:
        return None


def gate_context_bloat(conn, base_agent_id: str, hard: float = _CTX_HARD,
                       window: int = _CTX_WINDOW) -> bool:
    """The operator's 'auto-recycle AT 80%' trigger. True iff FRESH telemetry proves
    the lane's context fraction >= hard. Fail-closed: no/stale/untrustworthy
    telemetry -> False (never recycle a lane we can't prove is bloated — that both
    matches 'at 80%' and never spends a lossless-but-not-free reset on a lane still
    early in its window). Reads cc_session_costs today; migrate to the SRE's
    canonical per-lane pct once Gap-1 lands so the window lives in ONE place."""
    frac = latest_context_frac(conn, base_agent_id, window)
    if frac is None:
        return False
    return frac >= hard


def build_boot_instruction(base_agent_id: str, handoff_path: str) -> str:
    """The boot string a freshly-cleared lane receives. Names the ABSOLUTE handoff path
    so the lane never resolves a cwd-relative 'reports/' to the wrong tree and read a
    stale handoff (op#14539 fix 1 — the cc-irsyad #27711 failure mode)."""
    return (f"You are {base_agent_id}, auto-recycled by the SRE (gated, CAI-681). "
            f"Read your fresh handoff at {handoff_path} IN FULL (absolute path — not your "
            f"cwd's reports/), reconcile your bus inbox (agent_messages "
            f"to_agent='{base_agent_id}'), then resume your PLAN.")


def compute_gates(conn, lane_row: dict, handoff_ref_epoch: "float | None" = None) -> dict:
    """All gates for a lane, each mechanically derived + fail-closed. The first
    three are the CAI-681 safety FLOOR (enforced by fleet_health_boundaries);
    context_bloat is the operator's 'at 80%' TRIGGER, layered on top in plan().

    handoff_ref_epoch (op#14539 fix 2): the reference the fresh_handoff gate measures the
    handoff mtime against. Default None -> use last_material_action_epoch (the autonomous
    detector's 'is an EXISTING handoff current?' question). The DRIVEN checkpoint path passes
    T_nudge instead, so a 'DONE' ack the lane posts AFTER writing its handoff cannot make a
    genuinely-fresh handoff read as stale."""
    session = lane_row.get("tmux_session") or lane_row.get("lane")
    base = lane_row.get("base_agent_id") or lane_row.get("lane")
    ref = handoff_ref_epoch if handoff_ref_epoch is not None \
        else last_material_action_epoch(conn, base)
    return {
        "idle": gate_idle(session) if session else False,
        "git_clean": gate_git_clean(lane_row.get("worktree_path")),
        "fresh_handoff": gate_fresh_handoff(base, lane_row.get("notes"), ref),
        "context_bloat": gate_context_bloat(conn, base),
    }


# ── audit (cond 4: written BEFORE the /clear, fail-closed-visible) ─────────────
def audit_before_clear(conn, base_agent_id: str, session: str, gates: dict, reason: str) -> None:
    """Write the attributable pre-clear audit row (actor + reason + gates). Raises
    on failure so a swallowed audit can NEVER precede a silent clear."""
    body = (f"SRE LANE RED-RESET (CAI-RESP-681) — recycling worker lane "
            f"'{base_agent_id}' (session {session}). Gates verified: {gates}. "
            f"Reason: {reason}")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
            "VALUES (%s,%s,%s,%s,%s,false,'P2')",
            [fhb.SRE_AGENT_ID, fhb.SRE_AGENT_ID, "update",
             f"SRE recycled lane {base_agent_id} (gated, CAI-681)", body])
    conn.commit()


# ── plan / execute ────────────────────────────────────────────────────────────
def plan(conn, lane_row: dict, armed: bool, require_bloat: bool = True,
         handoff_ref_epoch: "float | None" = None) -> dict:
    """Compute gates + ask the boundary whether a red-reset is permitted, THEN apply
    the context-bloat trigger on top. Never mutates. Returns {lane, session, gates,
    permitted, reason}. require_bloat=False is the manual wedged-lane path (--lane X
    --ignore-context): recycle a stuck-but-not-bloated lane, safety floor still enforced.
    handoff_ref_epoch is passed through to compute_gates (op#14539 fix 2 — the driven
    checkpoint path measures handoff freshness against T_nudge)."""
    session = lane_row.get("tmux_session") or lane_row.get("lane")
    base = lane_row.get("base_agent_id") or lane_row.get("lane")
    gates = compute_gates(conn, lane_row, handoff_ref_epoch=handoff_ref_epoch)
    permitted, reason = True, "all gates verified; armed" if armed else "DISARMED (detect-only)"
    # 1) CAI-681 safety FLOOR (idle+git_clean+fresh_handoff+armed) — unchanged.
    try:
        fhb.assert_sre_lane_red_permitted(base, gates, armed, identity=fhb.SRE_AGENT_ID)
    except fhb.BoundaryViolation as e:
        permitted, reason = False, str(e)
    # 2) operator's 'at 80%' TRIGGER, layered on top of the floor (skippable only on
    #    the explicit manual wedged-lane path). Fail-closed: unmeasurable -> skip.
    if permitted and require_bloat and not gates["context_bloat"]:
        permitted = False
        reason = (f"context below {int(_CTX_HARD * 100)}% (or unmeasurable) — not "
                  f"bloated, skip; use --ignore-context to force a wedged-lane recycle")
    return {"lane": lane_row.get("lane"), "base": base, "session": session,
            "gates": gates, "permitted": permitted, "reason": reason}


def _self_test() -> int:
    """Offline proof of the fail-closed gate computation (no DB, no tmux)."""
    fails = []
    def ok(c, m):
        (print(f"  [{'PASS' if c else 'FAIL'}] {m}"), fails.append(m) if not c else None)
    ok(gate_git_clean(None) is False, "git_clean(None) fails closed")
    ok(gate_git_clean("/no/such/worktree/xyz") is False, "git_clean(missing dir) fails closed")
    ok(gate_idle("no-such-session-xyz") is False, "idle(dead session) fails closed")
    ok(gate_fresh_handoff("cc-nobody", None, None) is False, "fresh_handoff(no handoff) fails closed")
    ok(gate_fresh_handoff("cc-nobody", None, 1.0) is False, "fresh_handoff(no file, has action) fails closed")
    # context-bloat trigger: unmeasurable -> fails closed (never recycle blind)
    ok(latest_context_frac(None, "cc-x") is None, "context_frac(no conn) is None")
    ok(gate_context_bloat(None, "cc-x") is False, "context_bloat(no conn) fails closed")
    ok(gate_context_bloat(None, "cc-x", hard=0.0) is False,
       "context_bloat fails closed even at hard=0 when unmeasurable")
    # boundary integration: disarmed always refuses even if gates were true
    try:
        fhb.assert_sre_lane_red_permitted("cc-lane", {"idle": True, "git_clean": True, "fresh_handoff": True},
                                          False, identity=fhb.SRE_AGENT_ID)
        ok(False, "disarmed must refuse")
    except fhb.BoundaryViolation:
        ok(True, "disarmed refuses via boundary")
    print()
    print("SRE_LANE_RECYCLE SELF-TEST " + ("FAILED" if fails else "PASSED"))
    return 1 if fails else 0


def discover_lanes(conn, lane: "str | None" = None) -> list:
    """Enumerate ACTUALLY-RUNNING worker lanes (OPTION 2, CAI-RESP-705): the freshest live
    instance per base from agent_status, cross-referenced to fleet_lanes ONLY for
    worktree_path/branch/notes. Correct-by-construction: no stale-registry blind spot. An
    unregistered running lane appears but has NO worktree_path -> gate_git_clean fails closed
    (fail-SAFE, not blind). Singletons AND the SRE itself (fhb.SINGLETON_BODIES) are excluded —
    defence-in-depth with assert_sre_never_targets_singleton. `lane` restricts to one
    fleet_lanes.lane. Shared by the CLI and the checkpoint-recycle driver's entrypoint."""
    singletons = sorted(fhb.SINGLETON_BODIES)
    ph = ",".join(["%s"] * len(singletons))
    q = ("SELECT COALESCE(l.lane, s.base_agent_id) AS lane, "
         "       l.worktree_path, l.branch, s.base_agent_id, l.desired_state, l.notes, "
         "       s.tmux_session "
         "FROM (SELECT DISTINCT ON (base_agent_id) base_agent_id, tmux_session "
         "      FROM agent_status "
         "      WHERE tmux_session IS NOT NULL AND base_agent_id IS NOT NULL "
         f"        AND base_agent_id NOT IN ({ph}) "
         "        AND last_heartbeat > now() - interval '30 minutes' "
         "      ORDER BY base_agent_id, last_heartbeat DESC) s "
         "LEFT JOIN LATERAL (SELECT lane, worktree_path, branch, notes, desired_state "
         "                   FROM fleet_lanes fl WHERE fl.base_agent_id = s.base_agent_id "
         "                   ORDER BY (worktree_path IS NOT NULL) DESC, lane LIMIT 1) l ON true")
    params: list = list(singletons)
    if lane:
        q += " WHERE COALESCE(l.lane, s.base_agent_id) = %s"; params.append(lane)
    q += " ORDER BY s.base_agent_id"
    with conn.cursor() as cur:
        cur.execute(q, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def armed_recycle(conn, lane_row: dict, *, reason: str,
                  handoff_ref_epoch: "float | None" = None) -> dict:
    """The armed checkpoint-recycle ACTION on ONE specific lane_row, in-process. Re-verifies the
    CAI-681 floor at the moment of action (require_bloat=False — the caller already established
    bloat/wedge), writes the audit row BEFORE the clear, then runs reset_lane.sh with the
    ABSOLUTE-path boot string. Returns {recycled, ...}. Shared by main()'s armed branch AND the
    checkpoint-recycle driver — so the driver recycles the EXACT session/worktree it discovered,
    never a re-discovered (DISTINCT-ON-ambiguous) one. Fail-closed: not-permitted -> no reset."""
    p = plan(conn, lane_row, armed=True, require_bloat=False, handoff_ref_epoch=handoff_ref_epoch)
    if not p["permitted"]:
        return {"recycled": False, "reason": p["reason"], "gates": p["gates"], "session": p["session"]}
    audit_before_clear(conn, p["base"], p["session"], p["gates"], reason)
    handoff_path = _newest_handoff_path(p["base"], lane_row.get("notes"))
    # STAGED handoff compaction (Nazim #31825, item-3): keep the restore point readable-whole
    # for the fresh lane, enforce-in-code. No-op when <=cap; the policy HOLDS cai. FAIL LOUD:
    # a compaction exception ABORTS this recycle (dead-man's-switch) — we never reset onto a
    # possibly half-written handoff. Runs AFTER the audit row, BEFORE the /clear.
    if handoff_path:
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            hcp.compact_if_enabled(handoff_path, agent=p["base"],
                                   session=p["session"], stamp=stamp)
        except Exception as e:  # noqa: BLE001 — fail loud, abort the recycle
            return {"recycled": False, "reason": f"handoff compaction failed: {e!r}",
                    "session": p["session"], "gates": p["gates"]}
    boot = build_boot_instruction(p["base"], handoff_path)
    r = subprocess.run(["bash", str(_ORCH_DIR / "scripts" / "reset_lane.sh"), p["session"], boot],
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout + r.stderr).strip()
    return {"recycled": r.returncode == 0, "rc": r.returncode, "session": p["session"],
            "boot": boot, "gates": p["gates"], "output": out}


def main() -> int:
    ap = argparse.ArgumentParser(description="SRE lanes-only auto-recycle (CAI-RESP-681)")
    ap.add_argument("--arm", action="store_true",
                    help="ARM the destructive /clear (cond 5). Default: detect-only.")
    ap.add_argument("--lane", help="restrict to one lane (by fleet_lanes.lane)")
    ap.add_argument("--ignore-context", action="store_true",
                    help="skip the >=80%% context-bloat trigger (manual wedged-lane "
                         "recycle path). The idle+git_clean+fresh_handoff safety floor "
                         "still applies. Default: only recycle genuinely-bloated lanes.")
    ap.add_argument("--reason", default="SRE auto-recycle: wedged/bloated lane",
                    help="audit reason recorded before any clear")
    ap.add_argument("--handoff-after", type=float, default=None,
                    help="epoch (T_nudge) the fresh_handoff gate measures against instead of the "
                         "lane's last bus message — the DRIVEN checkpoint path (op#14539 fix 2). "
                         "Ignores a post-handoff DONE ack that would otherwise fail the gate.")
    ap.add_argument("--self-test", action="store_true", help="run offline gate self-test + exit")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    armed = bool(args.arm) or os.environ.get("CTX_WD_LANE_ARM") == "1"
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr); return 2
    conn = psycopg.connect(dsn)

    lanes = discover_lanes(conn, args.lane)

    require_bloat = not args.ignore_context
    print(f"SRE lane-recycle — {'ARMED' if armed else 'DETECT-ONLY (disarmed)'} — "
          f"trigger={'>=' + str(int(_CTX_HARD * 100)) + '% context' if require_bloat else 'wedged (context ignored)'} — "
          f"{len(lanes)} lane(s) · {datetime.now(timezone.utc).isoformat()}")
    acted = 0
    for lr in lanes:
        p = plan(conn, lr, armed, require_bloat=require_bloat,
                 handoff_ref_epoch=args.handoff_after)
        base = p["base"]
        frac = latest_context_frac(conn, base)
        pct = f"{frac * 100:.0f}%" if frac is not None else "?%"
        mark = "RECYCLE" if p["permitted"] else "skip"
        print(f"  [{mark}] {base:22s} ctx={pct:>4s} session={p['session']} gates={p['gates']}")
        if not p["permitted"]:
            print(f"            reason: {p['reason']}")
            continue
        if not armed:
            print("            would recycle (disarmed — not touching it)")
            continue
        # ARMED + permitted: audit BEFORE clear (cond 4), then reset_lane.sh — via the shared
        # armed_recycle action (re-verifies the floor at the moment of action).
        res = armed_recycle(conn, lr, reason=args.reason, handoff_ref_epoch=args.handoff_after)
        print(f"            reset_lane.sh rc={res.get('rc')} recycled={res.get('recycled')} "
              f":: {(res.get('output') or '').splitlines()[-1:]}")
        acted += 1
    print(f"done — {acted} recycled, {len(lanes)-acted} left alone.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
