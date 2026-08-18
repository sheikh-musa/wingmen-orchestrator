#!/usr/bin/env python3
"""checkpoint_recycle_driver.py — hands-off checkpoint-first recycle of an active-red WORKER lane.

Phase 2 of op#14539 / bus #27720 item-1. This closes the "who writes the handoff" gap:
`sre_lane_recycle.py` only VERIFIES a pre-existing fresh handoff; the detectors only WARN.
Nothing in the automated worker path WROTE the handoff — a human (the SRE) did, by hand
(the manual cc-irsyad recycle #27711). This module automates exactly that loop:

    durable bus note -> nudge the lane to self-handoff (ABSOLUTE path) -> block-wait for a
    FRESH, non-stub handoff -> re-verify the CAI-681 floor at the moment of action
    (freshness measured against T_nudge — the Phase-1 fix) -> recycle via the PROVEN gated
    recycler (which re-checks every gate + writes the audit row + runs reset_lane.sh with the
    absolute-path boot string) -> return a structured outcome.

DESIGN PRINCIPLES (charter §2/§3):
  * FAIL-CLOSED, dead-man's-switch: ANY step that fails or cannot be evaluated ABORTS the
    action LOUD, touches nothing further, and leaves the world in its pre-action state. No
    step is swallowed behind a default-OK.
  * DEPENDENCY-INJECTED side effects (now/sleep/nudge/post-note/recycle/handoff-path) so the
    whole pipeline is unit-testable with no real tmux/DB/clock/subprocess, and so the ARMED
    path is a single explicit seam.
  * WORKERS ONLY: assert_sre_never_targets_singleton is called FIRST, every time —
    cai/hub/Nazim/SRE-self are structurally unreachable.
  * DRY-RUN (armed=False) computes selection + entry gates and reports WOULD-recycle; it
    posts no note, sends no nudge, recycles nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ORCH_DIR / "scripts"))
sys.path.insert(0, str(_ORCH_DIR / "scripts" / "lib"))
import sre_lane_recycle as slr            # noqa: E402
import fleet_health_boundaries as fhb     # noqa: E402

# A lossless handoff is prose with task state + SHAs; a near-empty file is a stub, not a
# restore point. Fail-closed under this floor (never recycle onto a stub handoff).
MIN_HANDOFF_BYTES = int(os.environ.get("CKPT_MIN_HANDOFF_BYTES", "800"))
_WAIT_S = int(os.environ.get("CKPT_WAIT_S", "240"))     # mirrors the singleton checkpoint wait
_POLL_S = int(os.environ.get("CKPT_POLL_S", "15"))


def _handoff_target_path(base_agent_id: str, notes: "str | None") -> str:
    """The ABSOLUTE path we ask the lane to WRITE its fresh handoff to. Matches the glob
    `_newest_handoff_path` reads, so the block-wait finds exactly what the lane wrote. Honors
    a `handoff_glob=` note override's directory; else the canonical reports/<short>-handoff-NOW.md."""
    short = base_agent_id[3:] if base_agent_id.startswith("cc-") else base_agent_id
    directory = _ORCH_DIR / "reports"
    if notes:
        for tok in notes.split():
            if tok.startswith("handoff_glob="):
                globpat = tok.split("=", 1)[1]
                directory = Path(os.path.dirname(str(_ORCH_DIR / globpat)))
                break
    return os.path.abspath(str(directory / f"{short}-handoff-NOW.md"))


def _checkpoint_nudge_msg(base_agent_id: str, notes: "str | None") -> str:
    target = _handoff_target_path(base_agent_id, notes)
    return (f"SRE checkpoint-first recycle incoming (hands-off, gated). Before I recycle you: "
            f"write a FRESH lossless self-handoff to the ABSOLUTE path {target} — capture your "
            f"tasks with exact state (branches/SHAs, what's in-progress), and confirm your "
            f"worktree is committed/clean. Then stay idle; I detect the file and recycle. "
            f"Lossless — you boot fresh and resume your PLAN from that handoff.")


def _handoff_ready(path: str, t_nudge: float) -> bool:
    """True iff `path` is a handoff written AT/AFTER we nudged AND large enough to be real.
    Fail-closed: any stat error, a stale mtime, or a sub-floor size -> False (keep waiting)."""
    try:
        st = os.stat(path)
    except Exception:
        return False
    return st.st_mtime >= t_nudge and st.st_size >= MIN_HANDOFF_BYTES


def _handoff_mtime_if_ready(path: "str | None", t_nudge: float) -> "float | None":
    """The handoff's mtime WHEN it is fresh (>=t_nudge) AND big enough (>=floor), else None.
    The wait loop compares this across consecutive polls: an UNCHANGED mtime means the write has
    SETTLED (complete), which guards against reading a partially-written handoff — the #27784
    race, where size+mtime tripped 'ready' mid-write and content-verify then read a file missing
    the branch line it was about to write. Fail-closed: stat error / stale / sub-floor -> None."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except Exception:
        return None
    if st.st_mtime >= t_nudge and st.st_size >= MIN_HANDOFF_BYTES:
        return st.st_mtime
    return None


def _git_branch(worktree_path: "str | None") -> "str | None":
    """The worktree's current branch, or None. Fail-closed: no path, not a dir, git error,
    or a detached HEAD ('HEAD') -> None, which the content check treats as unverifiable."""
    if not worktree_path or not os.path.isdir(worktree_path):
        return None
    try:
        r = subprocess.run(["git", "-C", worktree_path, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    br = r.stdout.strip()
    return br or None if br != "HEAD" else None


def _handoff_content_ok(handoff_text: str, branch: "str | None") -> bool:
    """A lossless handoff NAMES the lane's current branch (op#14539 Phase 3, Nazim-endorsed
    content-sanity proxy). True iff branch is determinable AND appears in the handoff text.
    Fail-closed: a falsy/undeterminable branch -> False (never recycle onto an unverifiable
    handoff — size+mtime alone would pass an 800B stub)."""
    return bool(branch) and branch in handoff_text


def verify_resume(base: str, read_ctx_frac_fn, *, max_frac: float = 0.5) -> bool:
    """Confirm the lane genuinely CAME BACK after the /clear — its context fraction DROPPED.
    True iff a real fresh reading exists AND is < max_frac. Fail-closed: an unmeasurable
    (None) or still-high reading -> False (resume NOT verified; the caller reports failure)."""
    try:
        frac = read_ctx_frac_fn(base)
    except Exception:
        return False
    return frac is not None and frac < max_frac


def _abort(stage: str, reason: str, t_nudge=None, gates=None) -> dict:
    return {"stage": stage, "outcome": "aborted", "reason": reason,
            "t_nudge": t_nudge, "gates": gates}


def drive_checkpoint_recycle(conn, lane_row: dict, *, armed: bool = False,
                             now_fn, sleep_fn, nudge_fn, post_note_fn, recycle_fn,
                             handoff_path_fn=slr._newest_handoff_path, branch_fn=_git_branch,
                             wait_s: int = _WAIT_S, poll_s: int = _POLL_S,
                             reason: str = "SRE hands-off checkpoint-first recycle (op#14539)") -> dict:
    """Drive a worker lane through checkpoint-first recycle. Returns
    {stage, outcome: recycled|dry-run|aborted, reason, t_nudge, gates[, result]}.

    Collaborators (injected): now_fn()->float; sleep_fn(seconds); nudge_fn(session,msg)->int
    (0 == verified-submit, mirrors lane_nudge.sh); post_note_fn(conn,base); recycle_fn(base,
    session,t_nudge)->result dict with result["recycled"] is True on success (wraps the gated
    sre_lane_recycle --arm --ignore-context --handoff-after T + resume verification);
    handoff_path_fn(base,notes)->abs path|None."""
    base = lane_row.get("base_agent_id") or lane_row.get("lane")
    session = lane_row.get("tmux_session") or lane_row.get("lane")
    notes = lane_row.get("notes")

    # ── STAGE 1: safety floor at entry (fail-closed) ──────────────────────────
    stage = "entry-gates"
    # (a) WORKERS ONLY — singleton guard raises; cai/hub/Nazim/SRE-self unreachable.
    try:
        fhb.assert_sre_never_targets_singleton(base, identity=fhb.SRE_AGENT_ID)
    except fhb.BoundaryViolation as e:
        return _abort(stage, f"singleton guard refused: {e}")
    # (b) idle + git-clean must both hold NOW (fresh_handoff is expected False — that's why we
    #     checkpoint). Reuse the proven, fail-closed gate probes.
    idle = slr.gate_idle(session) if session else False
    if not idle:
        return _abort(stage, "lane not idle at entry (fail-closed) — will not checkpoint a working body")
    git_clean = slr.gate_git_clean(lane_row.get("worktree_path"))
    if not git_clean:
        return _abort(stage, "worktree not git-clean at entry (fail-closed) — uncommitted work at risk")
    entry_gates = {"idle": True, "git_clean": True}

    # ── DRY-RUN: touch NOTHING beyond entry gates ─────────────────────────────
    if not armed:
        return {"stage": stage, "outcome": "dry-run", "t_nudge": None, "gates": entry_gates,
                "reason": "WOULD drive checkpoint-then-recycle (entry gates pass; disarmed — nothing touched)"}

    # ── ARMED. Any failure below => abort LOUD, touch nothing further. ─────────
    # STAGE 2: durable continuity bus note (the safety net that let cc-irsyad self-correct).
    stage = "post-note"
    try:
        post_note_fn(conn, base)
    except Exception as e:
        return _abort(stage, f"durable bus note failed — no nudge, no recycle: {e!r}", gates=entry_gates)

    # STAGE 3: record T_nudge, then nudge the lane to self-handoff to the ABSOLUTE path.
    stage = "nudge"
    t_nudge = now_fn()
    try:
        rc = nudge_fn(session, _checkpoint_nudge_msg(base, notes))
    except Exception as e:
        return _abort(stage, f"nudge raised — no recycle: {e!r}", t_nudge=t_nudge, gates=entry_gates)
    if rc != 0:
        return _abort(stage, f"nudge did not verify-submit (rc={rc}) — no recycle", t_nudge=t_nudge, gates=entry_gates)

    # STAGE 4: block-wait for a FRESH, non-stub handoff whose write has SETTLED. A lane writes its
    #          handoff incrementally, so size>=floor + mtime>=T_nudge can trip while the file is
    #          STILL being written; reading then gave the #27784 false-negative (content-verify
    #          saw a file missing the branch line it was about to write). Require the mtime to be
    #          UNCHANGED across a poll interval (the write has settled) before declaring ready, so
    #          STAGE 4b always reads a COMPLETE handoff.
    stage = "wait-handoff"
    deadline = t_nudge + wait_s
    ready = False
    prev_mtime = None
    while now_fn() <= deadline:
        mt = _handoff_mtime_if_ready(handoff_path_fn(base, notes), t_nudge)
        if mt is not None and mt == prev_mtime:   # mtime stable across a poll -> write complete
            ready = True
            break
        prev_mtime = mt
        sleep_fn(poll_s)
    if not ready:
        return _abort(stage, f"no fresh SETTLED handoff (mtime>=T_nudge, size>={MIN_HANDOFF_BYTES}B, "
                             f"mtime stable across a poll) within {wait_s}s — NO recycle (dead-man's-switch)",
                      t_nudge=t_nudge, gates=entry_gates)
    p = handoff_path_fn(base, notes)

    # STAGE 4b: content-verify — the fresh, big-enough handoff must NAME the lane's current
    #           branch, so a size+mtime-passing STUB/garbage can never become a restore point
    #           (Nazim #27750). Fail-closed on unreadable file or undeterminable branch.
    stage = "content-verify"
    try:
        handoff_text = Path(p).read_text(errors="replace")
    except Exception as e:
        return _abort(stage, f"handoff unreadable — no recycle: {e!r}", t_nudge=t_nudge, gates=entry_gates)
    branch = branch_fn(lane_row.get("worktree_path"))
    if not _handoff_content_ok(handoff_text, branch):
        return _abort(stage, f"handoff does not name the lane's branch (branch={branch!r}) — "
                             f"content-unverified, NO recycle", t_nudge=t_nudge, gates=entry_gates)

    # STAGE 5: re-verify the full floor at the MOMENT of action; freshness vs T_nudge (fix 2).
    stage = "reverify"
    p = slr.plan(conn, lane_row, armed=True, require_bloat=False, handoff_ref_epoch=t_nudge)
    if not p["permitted"]:
        return _abort(stage, f"moment-of-action gates refused: {p['reason']}",
                      t_nudge=t_nudge, gates=p["gates"])

    # STAGE 6: armed recycle via the PROVEN gated recycler (re-checks gates, audits, reset_lane.sh
    #          with the absolute-path boot string) + injected resume verification.
    stage = "recycle"
    try:
        result = recycle_fn(base, session, t_nudge)
    except Exception as e:
        return _abort(stage, f"recycle failed — the gated recycler leaves the lane in its "
                             f"pre-recycle state: {e!r}", t_nudge=t_nudge, gates=p["gates"])
    if not (isinstance(result, dict) and result.get("recycled") is True):
        return _abort(stage, f"recycle did not confirm success (no resume verified): {result!r}",
                      t_nudge=t_nudge, gates=p["gates"])

    return {"stage": "done", "outcome": "recycled",
            "reason": "checkpoint-first recycle complete + resume verified",
            "t_nudge": t_nudge, "gates": p["gates"], "result": result}


# ── production entrypoint (Phase 3C): thin main() binding the REAL collaborators ──────────────
_NOTE_SUBJECT = "SRE checkpoint-first recycle incoming — write a fresh handoff first (lossless)"
_NOTE_BODY = (
    "TL;DR: the SRE is about to checkpoint-recycle you (hands-off, gated). It is LOSSLESS — write a "
    "FRESH self-handoff first and you lose nothing. AFTER the recycle: read your fresh handoff IN FULL, "
    "reconcile your bus inbox, then RESUME YOUR PLAN — do not let inbox-chasing override your in-progress work."
)


def _live_session_cwd(session: str) -> "str | None":
    """The session's ACTUAL live cwd (tmux pane_current_path) — GROUND TRUTH for which worktree
    this pane is in. The manual cc-irsyad recycle proved the live cwd is authoritative over
    fleet_lanes' multiple rows per base. Fail-closed: any tmux error / empty -> None."""
    try:
        r = subprocess.run([slr.TMUX, "display-message", "-p", "-t", session, "#{pane_current_path}"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    cwd = r.stdout.strip()
    return cwd or None


def discover_session_lane(conn, session: str, *, cwd_fn=_live_session_cwd) -> "dict | None":
    """Build a CORRECT lane_row for ONE live tmux session — the fix for the DISTINCT-ON-base
    bug where a multi-session base (cc-irsyad) collapsed to a row pairing one session with a
    DIFFERENT lane's worktree. Here lane==session and worktree_path is the session's OWN live
    cwd, so idle(session) and git_clean(worktree) always refer to the SAME lane.

    Fail-closed at every step: the session must be a live worker (fresh heartbeat, not a
    singleton); its cwd must be readable; and if fleet_lanes names a worktree for lane==session
    it MUST resolve to the same directory as the live cwd (disagreement -> None, never guess)."""
    with conn.cursor() as cur:
        cur.execute("SELECT base_agent_id FROM agent_status WHERE tmux_session=%s "
                    "AND last_heartbeat > now() - interval '30 minutes' "
                    "ORDER BY last_heartbeat DESC LIMIT 1", [session])
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        base = row[0]
        if base in fhb.SINGLETON_BODIES:      # never target a singleton via this path
            return None
        worktree = cwd_fn(session)
        if not worktree:                       # can't verify the tree -> won't recycle it
            return None
        cur.execute("SELECT worktree_path, notes FROM fleet_lanes "
                    "WHERE base_agent_id=%s AND lane=%s LIMIT 1", [base, session])
        fl = cur.fetchone()
    notes = None
    if fl:
        fl_wt, notes = fl[0], (fl[1] if len(fl) > 1 else None)
        if fl_wt and os.path.realpath(fl_wt) != os.path.realpath(worktree):
            return None                        # session<->worktree disagreement -> ambiguous
    return {"lane": session, "base_agent_id": base, "tmux_session": session,
            "worktree_path": worktree, "notes": notes}


def run(args, conn) -> dict:
    """Bind the REAL collaborators and drive one lane. Dry-run (args.arm False) short-circuits
    inside the driver after the entry gates, so none of the real side effects below ever fire."""
    row = discover_session_lane(conn, args.session)
    if row is None:
        return {"stage": "discover", "outcome": "aborted",
                "reason": f"session {args.session!r} not a discoverable live worker lane "
                          f"(dead/singleton/unreadable-cwd/worktree-disagreement — fail-closed)"}
    base = row.get("base_agent_id") or row.get("lane")
    try:
        fhb.assert_sre_never_targets_singleton(base, identity=fhb.SRE_AGENT_ID)
    except fhb.BoundaryViolation as e:
        return {"stage": "discover", "outcome": "aborted", "reason": f"singleton refused: {e}"}
    reason = args.reason

    def nudge_fn(session, msg):
        r = subprocess.run(["bash", str(_ORCH_DIR / "scripts" / "lane_nudge.sh"), session, msg],
                           capture_output=True, text=True, timeout=90)
        return r.returncode

    def post_note_fn(_conn, _base):
        with _conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id',%s,true)", [fhb.SRE_AGENT_ID])
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                "requires_response,priority) VALUES (%s,%s,'update',%s,%s,true,'P2')",
                [fhb.SRE_AGENT_ID, _base, _NOTE_SUBJECT, _NOTE_BODY])
        _conn.commit()

    def recycle_fn(_base, _session, t_nudge):
        # Recycle the EXACT discovered lane_row IN-PROCESS (no CLI re-discovery, which would
        # re-hit the DISTINCT-ON-base ambiguity). armed_recycle re-verifies the floor + audits.
        res = slr.armed_recycle(conn, row, reason=reason, handoff_ref_epoch=t_nudge)
        if not res.get("recycled"):
            return {**res, "recycled": False}
        # verify came-back at the process level: context fraction DROPPED (bounded poll — telemetry lags).
        resumed = False
        for _ in range(8):
            if verify_resume(_base, lambda b: slr.latest_context_frac(conn, b)):
                resumed = True
                break
            time.sleep(15)
        return {**res, "recycled": resumed, "resume_verified": resumed}

    return drive_checkpoint_recycle(
        conn, row, armed=bool(args.arm),
        now_fn=time.time, sleep_fn=time.sleep,
        nudge_fn=nudge_fn, post_note_fn=post_note_fn, recycle_fn=recycle_fn,
        wait_s=args.wait_s, poll_s=args.poll_s, reason=reason)


def _build_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Hands-off checkpoint-first recycle of a WORKER lane (op#14539 / #27720 item-1).")
    ap.add_argument("--session", required=True,
                    help="the live tmux SESSION to recycle (targeted per-session, ground-truth cwd)")
    ap.add_argument("--arm", action="store_true",
                    help="ARM the real checkpoint+recycle. DEFAULT: dry-run (touches nothing).")
    ap.add_argument("--wait-s", type=int, default=_WAIT_S)
    ap.add_argument("--poll-s", type=int, default=_POLL_S)
    ap.add_argument("--reason", default="SRE hands-off checkpoint-first recycle (op#14539)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _build_args(argv)
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        out = run(args, conn)
    finally:
        conn.close()
    print(json.dumps(out, default=str, indent=2))
    return 0 if out.get("outcome") in ("recycled", "dry-run") else 1


if __name__ == "__main__":
    sys.exit(main())
