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

import os
import sys
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


def _abort(stage: str, reason: str, t_nudge=None, gates=None) -> dict:
    return {"stage": stage, "outcome": "aborted", "reason": reason,
            "t_nudge": t_nudge, "gates": gates}


def drive_checkpoint_recycle(conn, lane_row: dict, *, armed: bool = False,
                             now_fn, sleep_fn, nudge_fn, post_note_fn, recycle_fn,
                             handoff_path_fn=slr._newest_handoff_path,
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

    # STAGE 4: block-wait for a FRESH, non-stub handoff (mtime >= T_nudge, size >= floor).
    stage = "wait-handoff"
    deadline = t_nudge + wait_s
    ready = False
    while now_fn() <= deadline:
        p = handoff_path_fn(base, notes)
        if p and _handoff_ready(p, t_nudge):
            ready = True
            break
        sleep_fn(poll_s)
    if not ready:
        return _abort(stage, f"no fresh handoff (mtime>=T_nudge, size>={MIN_HANDOFF_BYTES}B) "
                             f"within {wait_s}s — NO recycle (dead-man's-switch)",
                      t_nudge=t_nudge, gates=entry_gates)

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
