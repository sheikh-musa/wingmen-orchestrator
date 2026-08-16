#!/usr/bin/env python3
"""lane_wedge_watchdog.py — detect + auto-recover the "idle-composer wedge".

THE PROBLEM it solves (2026-07-29 incident): a CC lane (a `claude` session in a
tmux session) sometimes STAGES its own next-step in its input composer but never
SUBMITS it. The main loop is idle, the process is alive, and the pane looks
EXACTLY like "idle because it finished its work" — so it sits there, not
progressing, indefinitely. That day it silently froze two demo-critical lanes for
~a day before a human noticed. None of the existing watchdogs catch this:
  * the context watchdog acts on token bloat,
  * the priority-SLA watchdog acts on stalled BUS messages,
  * the fleet-stall/lane watchdogs act on DEAD processes.
A wedged lane is alive, has plenty of context, and may have an empty bus — every
existing gauge reads it green. This watchdog is the missing detector: it reads the
pane, recognises "idle + a NON-EMPTY composer that has not changed for minutes",
and (when armed) submits the lane's own staged step so the wedge self-heals in
minutes instead of costing a day.

  ┌─ SAFETY / STATUS ─────────────────────────────────────────────────────────┐
  │ SHIPS DRY-RUN. Default = detect + log + (opt-in) alert; takes NO submit/    │
  │ reset action. The reviewer arms auto-recovery later with --arm.             │
  │  - (no flag / default) : DRY-RUN. Classify every lane, log, and — with      │
  │    --alert — page the operator on a detected wedge / repeat-wedge / a       │
  │    watchdog-was-down gap. NEVER touches a lane.                             │
  │  - --arm               : enable the recovery ladder (Stage 1 verified       │
  │    submit -> Stage 2 guarded reset). GATED on the fleet_health_lease, the   │
  │    same single-owner pen the other SRE watchdogs act under (CAI-RESP-501).  │
  │  Detection + the safety alerts are UNGATED (a wedge page is never silenced  │
  │  by lease state); only the ACTIONS are lease-gated.                         │
  └────────────────────────────────────────────────────────────────────────────┘

DETECTION (the hard part is avoiding a false positive — this watchdog auto-acts on
a live agent session, so a false positive is its OWN failure):
  WORKING  footer shows "esc to interrupt" (or background agents in flight, or a
           queued message) -> NEVER touched, and any wedge episode is cleared.
  IDLE     footer shows "← for agents" and NOT "esc to interrupt".
    - EMPTY composer  -> idle/done. Left alone (this is scholar's normal state).
      ROOT-CAUSE NOTE (2026-07-29 investigation): "empty" means empty of REAL
      typed text. An idle lane paints its most-recent history entry as a DIM
      (SGR-2) ghost/autosuggestion into an EMPTY input buffer — a plain
      `capture-pane -p` cannot see the dim styling, so the first cut of this
      watchdog read the ghost as staged input and declared a PHANTOM wedge that no
      Enter could ever "submit" (the buffer is genuinely empty). Detection now
      reads the SGR-preserving `-e` capture and treats a fully-dim composer as
      empty (see _extract_composer / _is_dim_ghost), mirroring composer_capture.sh.
    - NON-EMPTY composer (REAL non-dim typed text) -> a WEDGE CANDIDATE. It becomes a WEDGE only when the
      SAME composer text AND the SAME whole-pane signature persist UNCHANGED across
      >= WEDGE_MIN_POLLS consecutive scans AND >= WEDGE_GRACE_SEC of wall-clock. A
      single snapshot never fires: a lane mid-typing or briefly between turns has a
      transient/changing composer, and any new pane output resets the episode. The
      per-lane (sig, composer, first_seen, poll_count) is tracked in a state file.

RECOVERY LADDER (only when --arm AND the fleet_health_lease is held):
  1. Stage 1 — VERIFIED SUBMIT the staged composer. It is the lane's OWN next
     step, so we do NOT retype it (that would destroy it) — we submit it and
     confirm the pane entered a working state (footer "esc to interrupt"), the
     same verified-submit heuristic as scripts/lane_nudge.sh. Usually a dropped
     Enter is all it was.
  2. Stage 2 — if still wedged STAGE2_DELAY_SEC after Stage 1: escalate to
     scripts/reset_lane.sh with a generic re-anchor. GUARD: only if the lane's git
     worktree is clean/committed (reset_lane preserves the composer to a log, but a
     /clear still discards the live conversation — never do it over uncommitted
     work). Dirty or indeterminate worktree -> DO NOT reset, alert instead.
  3. Repeat wedges — if the same lane wedges >= REPEAT_K times in REPEAT_WINDOW_SEC,
     STOP auto-acting on it and PAGE the operator (a lane that keeps re-wedging is a
     deeper problem than a dropped Enter).

DEAD-MAN'S-SWITCH (a silently-dead wedge-watchdog gives false confidence —
feedback_monitors_need_deadmans_switch): every run writes a heartbeat file, and if
the gap since the last heartbeat exceeds DEADMAN_GAP_SEC (watchdog was down/stalled
and just came back, or launchd had to restart it) the operator is paged. Any
unhandled exception pages loudly via the dependency-free subprocess path (the
__main__ wrapper), so a crash in the guard surfaces itself.

Mirrors the sibling watchdogs' conventions exactly: stdlib + psycopg via
DATABASE_URL/SUPABASE_DB_URL in .env, logs/ file logging, a JSON state file across
scans, fleet_health_lease action-gating, nazim_send.sh for operator pages. Designed
for a ~60s launchd cadence (dev.wingmen.lane-wedge-watchdog).

TWO-TMUX NOTE: Mini lanes live on the /usr/local/bin/tmux server (socket
tmux-501/default); /opt/homebrew/bin/tmux sees NONE of them (it is a different
server) — see reference_mini_tmux_two_binaries_socket. We resolve the binary the
same way reset_lane.sh does: /usr/local/bin/tmux, overridable via LANE_WEDGE_TMUX.

Usage:
    lane_wedge_watchdog.py                 # DRY-RUN: classify + log only (ships this)
    lane_wedge_watchdog.py --alert         # + page operator on wedge / repeat / deadman
    lane_wedge_watchdog.py --json          # machine-readable classification
    lane_wedge_watchdog.py --arm           # ARM recovery ladder (lease-gated). Reviewer only.
    lane_wedge_watchdog.py --self-test     # offline state-machine checks (no DB, no tmux)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.lib import fire_window  # noqa: E402  (quiesce during a recycle fire window)
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ORCH_DIR = Path(__file__).resolve().parent.parent
# Self-contained package import: under launchd there is NO PYTHONPATH, and the
# context watchdog hit 94% unwarned on 2026-07-21 because `import nervous_system`
# ModuleNotFound'd and its whole alert path crashed silently. Never depend on the
# env for our own imports.
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_ORCH_DIR / ".env")

# CAI-RESP-501: recovery ACTIONS are the watchdog pen (iii) ACTING — gated on the
# fleet_health_lease single-owner lease (default holder cc-fleet-health; hub
# reclaims on expiry) so the SRE and a reclaiming hub never both act on a lane.
from scripts.lib import fleet_health_lease  # noqa: E402

STATE_FILE = _ORCH_DIR / "logs" / "lane_wedge_watchdog_state.json"
LOG_FILE = _ORCH_DIR / "logs" / "lane_wedge_watchdog.log"
HEARTBEAT_FILE = _ORCH_DIR / "logs" / "lane_wedge_watchdog_heartbeat"


# ---------------------------------------------------------------------------
# Config — all overridable via environment (launchd EnvironmentVariables).
# ---------------------------------------------------------------------------

def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# A wedge is declared only when BOTH hold: the composer+pane have been byte-stable
# across this many consecutive scans, AND this many seconds have elapsed since the
# episode began. At a 60s launchd cadence, 4 polls + 300s ~= 5 minutes of a truly
# frozen, idle, staged pane before we touch it. Two independent floors (count AND
# wall-clock) so neither a burst of fast scans nor one long-delayed scan can fire
# it early. This is the primary false-positive guard — widen, never narrow, unless
# you have measured that lanes never sit legitimately-staged this long.
WEDGE_MIN_POLLS = _envint("LANE_WEDGE_MIN_POLLS", 4)
WEDGE_GRACE_SEC = _envint("LANE_WEDGE_GRACE_SEC", 300)  # 5 min

# After a Stage-1 verified-submit, wait this long before escalating to a Stage-2
# reset — gives the lane time to actually start working on its submitted step.
STAGE2_DELAY_SEC = _envint("LANE_WEDGE_STAGE2_DELAY_SEC", 180)  # 3 min

# Repeat-wedge circuit breaker: a lane that wedges this many times inside the
# window is a deeper problem than a dropped Enter — stop auto-acting on it and page.
REPEAT_K = _envint("LANE_WEDGE_REPEAT_K", 3)
REPEAT_WINDOW_SEC = _envint("LANE_WEDGE_REPEAT_WINDOW_SEC", 6 * 3600)  # 6h

# Dead-man's-switch: if a scan starts more than this long after the previous
# heartbeat, the watchdog was down/stalled — page (once) that it was blind. 4x the
# 60s cadence tolerates a normal skipped run; a real outage is far larger.
DEADMAN_GAP_SEC = _envint("LANE_WEDGE_DEADMAN_GAP_SEC", 240)

# Prune per-lane state / wedge history not seen for this long.
STATE_TTL_SEC = _envint("LANE_WEDGE_STATE_TTL_SEC", 24 * 3600)

# Sessions that are NOT recoverable build lanes and must NEVER be wedge-recovered
# here: the console (self), the governance singleton, the SRE, the hub. They have
# their own body-specific reset paths and their own operators; a generic
# submit/reset on them is exactly the kind of cross-body action ORCH-TOPOLOGY-001
# forbids. Excluded from detection-that-acts (still visible in --json for symmetry).
EXCLUDE_SESSIONS = {
    s.strip() for s in os.environ.get(
        "LANE_WEDGE_EXCLUDE",
        "nazim,cai,fleet-health,orch,orchestrator,infra").split(",") if s.strip()
}

# tmux binary — Mini lanes live on the /usr/local/bin/tmux server (see module
# docstring + reference_mini_tmux_two_binaries_socket). Resolve it the way
# reset_lane.sh does; never a bare `tmux` (PATH may point at the homebrew server
# that sees none of the lanes).
def _tmux_bin() -> str:
    override = os.environ.get("LANE_WEDGE_TMUX")
    if override:
        return override
    if os.path.exists("/usr/local/bin/tmux"):
        return "/usr/local/bin/tmux"
    return shutil.which("tmux") or "/usr/local/bin/tmux"


TM = _tmux_bin()

# Footer / composer markers — matched against the observable Claude-Code TUI, the
# same heuristics lane_nudge.sh and composer_capture.sh use.
_BUSY_MARKER = "esc to interrupt"       # foreground turn running
_IDLE_MARKER = "for agents"             # idle footer ("← for agents")
_QUEUED_MARKER = "queued message"       # busy lane accepted input into its queue
_BG_AGENT_MARKER = "background agent"    # "Waiting for N background agents to finish"
_COMPOSER_PLACEHOLDERS = ("Try ", "? for", "Press up to edit")

# ANSI/SGR handling — the composer's dim-ghost styling is only visible in a
# `capture-pane -p -e` (SGR-preserving) capture. We strip SGR for marker matching
# and the pane signature, but INSPECT it for the dim-ghost test below.
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # OSC (e.g. hyperlinks)
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")          # CSI incl. SGR
_DIM_SGR = "\x1b[2m"                                              # SGR 2 = faint/dim


def _strip_ansi(s: str) -> str:
    """Remove OSC + CSI/SGR escapes and normalise NBSP -> space, so SGR-preserving
    (`-e`) captures can be matched/sig'd exactly like a plain capture."""
    s = _ANSI_OSC_RE.sub("", s)
    s = _ANSI_CSI_RE.sub("", s)
    return s.replace(" ", " ")


def _composer_after_raw(comp_line: str) -> str:
    """The raw (SGR-bearing) content after the '❯' prompt glyph + its NBSP gap."""
    idx = comp_line.find("❯")
    if idx < 0:
        return ""
    after = comp_line[idx + len("❯"):]
    if after.startswith(" ") or after.startswith(" "):
        after = after[1:]
    return after


def _is_dim_ghost(after_raw: str) -> bool:
    """True iff the composer content is ENTIRELY dim (SGR 2) — Claude Code's
    ghost/autosuggestion (its most-recent history entry) painted into an EMPTY
    input buffer, NOT text the agent typed. Mirrors composer_capture.sh:_cc_dim_only
    (the fleet's verified convention: dim after ❯ == placeholder/ghost, non-dim ==
    real input). This is THE 2026-07-29 root-cause guard: without it the ghost reads
    as a staged step and a bare Enter can never submit it (the buffer is empty)."""
    if not after_raw.startswith(_DIM_SGR):
        return False
    # Drop the first dim run (ESC[2m ... up to a reset/normal-intensity or EOL),
    # then any remaining escape noise; if nothing printable is left, it was all dim.
    tmp = re.sub(r"\x1b\[2m.*?(?:\x1b\[0m|\x1b\[22m|$)", "", after_raw, count=1, flags=re.S)
    return _strip_ansi(tmp).strip() == ""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _tmux(*args: str, timeout: int = 20) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run([TM, *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception:
        s = {}
    s.setdefault("lanes", {})
    s.setdefault("wedge_history", {})
    s.setdefault("deadman", {})
    return s


def save_state(state: dict) -> None:
    now = time.time()
    state["lanes"] = {
        k: v for k, v in state.get("lanes", {}).items()
        if now - v.get("last_seen", now) < STATE_TTL_SEC
    }
    # prune wedge history to the repeat window (a lane that stopped wedging clears)
    state["wedge_history"] = {
        k: [t for t in v if now - t < REPEAT_WINDOW_SEC]
        for k, v in state.get("wedge_history", {}).items()
    }
    state["wedge_history"] = {k: v for k, v in state["wedge_history"].items() if v}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log(f"state-write-failed: {e}")


# ---------------------------------------------------------------------------
# Observation — read a lane's pane and classify it. Pure-ish (only side effect is
# the read-only capture-pane). Never mutates a lane.
# ---------------------------------------------------------------------------

@dataclass
class Obs:
    session: str
    reachable: bool
    working: bool             # foreground turn / bg agents / queued -> never touch
    composer: str             # unsent composer text ("" = empty / placeholder)
    sig: str                  # sha1 of the whole pane (episode-identity + activity)
    reason: str = ""          # human note on the working/idle call

    @property
    def idle_empty(self) -> bool:
        return (not self.working) and self.composer == ""

    @property
    def idle_staged(self) -> bool:
        return (not self.working) and self.composer != ""


def _extract_composer(pane_e: str) -> str:
    """UNSENT composer text from the Claude-Code prompt box — DIM-AWARE.

    Takes the SGR-preserving (`capture-pane -p -e`) pane. Modern TUI: the composer
    renders as the prompt glyph + U+00A0 (NON-BREAKING SPACE) + text. Returns "" for:
      * an empty box,
      * a DIM (SGR-2) ghost/autosuggestion — Claude Code paints its most-recent
        history entry, dimmed, into an EMPTY buffer (THE 2026-07-29 root cause: a
        plain `-p` capture cannot see the dim, so the ghost read as a staged step
        and no Enter could 'submit' the empty buffer), or
      * a dimmed placeholder ('Try "..."', '? for shortcuts', 'Press up to edit
        queued messages').
    Returns the real text only for genuinely-typed, non-dim input. Conservative —
    any doubt -> "" — a false 'staged' is this watchdog's own failure mode. The
    dim test mirrors composer_capture.sh:_cc_dim_only so the fleet reads the TUI
    one way."""
    comp_line = None
    for line in reversed(pane_e.splitlines()):
        if "❯" in line:
            comp_line = line
            break
    if comp_line is not None:
        after_raw = _composer_after_raw(comp_line)
        if _is_dim_ghost(after_raw):
            return ""  # dim ghost/autosuggestion over an EMPTY buffer — not staged
        after = _strip_ansi(after_raw).strip()
        if not after or any(after.startswith(p) for p in _COMPOSER_PLACEHOLDERS):
            return ""
        return after
    # legacy box-rule prompt (older TUI / a capture without the prompt glyph)
    for line in reversed(_strip_ansi(pane_e).splitlines()):
        if "│ >" in line or "│>" in line:
            after = line.split(">", 1)[1].replace("│", " ").strip()
            if not after or any(after.startswith(p) for p in _COMPOSER_PLACEHOLDERS):
                return ""
            return after
    return ""


def observe(session: str) -> Obs:
    # SGR-preserving capture: the dim-ghost test needs the escape data (see
    # _extract_composer). Fall back to a plain capture if a tmux/term returns none.
    r = _tmux("capture-pane", "-t", session, "-p", "-e")
    if r is None or r.returncode != 0:
        r = _tmux("capture-pane", "-t", session, "-p")
        if r is None or r.returncode != 0:
            return Obs(session, reachable=False, working=False, composer="", sig="")
    pane_e = r.stdout
    # SGR-stripped view for marker matching AND the pane signature — so pure colour
    # flicker (e.g. a ghost suggestion repainting) never churns the episode identity,
    # while real new output still changes the sig.
    pane = _strip_ansi(pane_e)
    low = pane.lower()
    lines = [ln for ln in pane.splitlines() if ln.strip()]
    tail = "\n".join(lines[-6:]).lower()

    # WORKING = any positive evidence of activity, so we NEVER touch a busy lane.
    # Asymmetric on purpose (matches composer_capture.sh): a false BUSY costs one
    # skipped recovery cycle; a false IDLE submits/resets into live work. Fail busy.
    busy = _BUSY_MARKER in tail
    queued = _QUEUED_MARKER in low
    # background-agent footer: scoped to the status region above the composer, and
    # only believed when NOT already flagged busy. A lane blocked on bg agents shows
    # no interrupt hint but must be treated as working (a submit/reset would discard
    # the agents' in-flight work — the 2026-07-25 cai near-miss).
    bg = _BG_AGENT_MARKER in low and "waiting for" in low
    working = busy or queued or bg
    reason = ""
    if busy:
        reason = "foreground turn ('esc to interrupt')"
    elif queued:
        reason = "queued message (busy queue)"
    elif bg:
        reason = "blocked on background agents"

    composer = "" if working else _extract_composer(pane_e)
    sig = hashlib.sha1(pane.encode("utf-8", "replace")).hexdigest()
    return Obs(session, reachable=True, working=working, composer=composer,
               sig=sig, reason=reason)


# ---------------------------------------------------------------------------
# Pure state machine — evaluate one lane's episode. Unit-tested offline; no DB,
# no tmux, no clock beyond the injected `now`.
# ---------------------------------------------------------------------------

# Verdicts
V_UNREACHABLE = "unreachable"
V_WORKING = "working"
V_IDLE_DONE = "idle-done"
V_MONITORING = "monitoring"      # staged, but not yet past the wedge threshold
V_WEDGE = "wedge"                # staged + stable past threshold -> recover


def evaluate(entry: Optional[dict], obs: Obs, now: float) -> "tuple[str, dict]":
    """Fold a fresh observation into this lane's episode state. Returns
    (verdict, new_entry). Pure — the runner decides what to DO with a wedge verdict.

    Episode identity is the whole-pane signature: any change (new output, composer
    edited, footer flipped) starts a NEW episode with poll_count=1 — so a lane that
    is progressing, or a composer still being typed, can never accumulate toward a
    wedge. Stage flags are carried only WITHIN one stable episode."""
    entry = dict(entry or {})
    entry["last_seen"] = now

    if not obs.reachable:
        # keep history but drop episode tracking
        for k in ("sig", "composer", "first_seen", "poll_count", "stage1_at",
                  "stage2_at", "alerted"):
            entry.pop(k, None)
        return V_UNREACHABLE, entry

    if not obs.idle_staged:
        # working OR idle-empty: no wedge possible; end any episode.
        for k in ("sig", "composer", "first_seen", "poll_count", "stage1_at",
                  "stage2_at", "alerted"):
            entry.pop(k, None)
        return (V_WORKING if obs.working else V_IDLE_DONE), entry

    # idle + non-empty composer -> candidate. Same episode iff sig unchanged.
    same_episode = entry.get("sig") == obs.sig and entry.get("composer") == obs.composer
    if same_episode:
        entry["poll_count"] = int(entry.get("poll_count", 0)) + 1
    else:
        entry["sig"] = obs.sig
        entry["composer"] = obs.composer
        entry["first_seen"] = now
        entry["poll_count"] = 1
        # new episode: clear per-episode recovery flags
        for k in ("stage1_at", "stage2_at", "alerted"):
            entry.pop(k, None)

    elapsed = now - float(entry.get("first_seen", now))
    is_wedge = entry["poll_count"] >= WEDGE_MIN_POLLS and elapsed >= WEDGE_GRACE_SEC
    return (V_WEDGE if is_wedge else V_MONITORING), entry


# ---------------------------------------------------------------------------
# Recovery actions (only reached when armed AND lease held). Each is a thin,
# audited wrapper over the existing verified-submit / reset tooling.
# ---------------------------------------------------------------------------

def _pane_working(session: str) -> bool:
    """Did the pane enter a working state? Reuses the lane_nudge.sh footer
    heuristic: working iff the last few non-blank lines show 'esc to interrupt'
    (and not the idle 'for agents'), OR the nudge was accepted into a busy queue."""
    r = _tmux("capture-pane", "-t", session, "-p")
    if r is None or r.returncode != 0:
        return False
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    tail = "\n".join(lines[-4:]).lower()
    if _QUEUED_MARKER in tail:
        return True  # accepted into queue == delivered/working
    return _BUSY_MARKER in tail and _IDLE_MARKER not in tail


def _composer_is_real(session: str) -> bool:
    """Does the composer hold REAL (non-dim, non-placeholder) staged text right
    now? Re-reads the SGR-preserving pane through the dim-aware extractor. Used to
    (a) guard Stage-1 against acting on a ghost that slipped detection, and
    (b) confirm a submit by seeing the composer go empty."""
    r = _tmux("capture-pane", "-t", session, "-p", "-e")
    if r is None or r.returncode != 0:
        return False
    return _extract_composer(r.stdout) != ""


def stage1_submit(session: str, tries: int = 3) -> tuple[bool, str]:
    """Stage 1 — VERIFIED SUBMIT the ALREADY-STAGED composer. We do NOT retype it:
    it is the lane's own next step and clobbering it would lose it.

    2026-07-29 root-cause note: the 'unsubmittable composer' that motivated this
    watchdog was, in every observed case, a DIM ghost autosuggestion over an EMPTY
    buffer (see _extract_composer) — nothing to submit, which is exactly why a bare
    Enter 'reliably failed'. Detection now filters those out, so if we reach here
    the composer holds REAL text and a single verified Enter submits it (measured:
    freshly/genuinely-typed text submits on the first Enter). We (a) re-confirm the
    composer is real before touching it — never submit a ghost — (b) send a
    cursor-to-end 'wake' (C-e: a real input event that does NOT alter content — a
    no-op on empty, end-of-line on text) then Enter, and (c) treat EITHER the footer
    entering a working state OR the composer going empty (the real text submitted)
    as success. Returns (recovered, detail)."""
    if not _composer_is_real(session):
        return False, "composer empty / dim-ghost — nothing to submit (not a real wedge)"
    # A recycle owns this pane while it wipes the composer and types /clear. Submitting
    # into that window jams the clear and the body returns half-initialised, so stand off
    # — the hold is seconds long and self-expiring, and the wedge will still be here after.
    if fire_window.is_held(session):
        return False, "recycle fire window held — standing off (will re-check next poll)"
    for t in range(1, tries + 1):
        _tmux("send-keys", "-t", session, "C-e")  # wake/refocus, content-preserving
        time.sleep(0.4)
        _tmux("send-keys", "-t", session, "Enter")
        time.sleep(4)
        if _pane_working(session) or not _composer_is_real(session):
            return True, f"submitted (try {t})"
        # a second Enter in case the TUI consumed the first as focus
        _tmux("send-keys", "-t", session, "Enter")
        time.sleep(3)
        if _pane_working(session) or not _composer_is_real(session):
            return True, f"submitted (try {t}, 2nd Enter)"
    return False, f"pane did not enter a working state after {tries} tries"


def _worktree_dir(session: str, lane_dirs: dict[str, str]) -> Optional[str]:
    """Resolve the lane's git worktree dir: the fleet_lanes.worktree_path if the
    session maps to a lane row, else the pane's current path. None if neither
    resolves."""
    d = lane_dirs.get(session)
    if d:
        return d
    r = _tmux("display-message", "-p", "-t", session, "#{pane_current_path}")
    if r and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


def _worktree_clean(session: str, lane_dirs: dict[str, str]) -> tuple[Optional[bool], str]:
    """Is the lane's git worktree clean/committed? Returns (clean, detail).
    clean is None when INDETERMINATE (no dir / not a repo / git failed) — the
    caller MUST treat None as 'do not reset' (fail-safe: never /clear over work we
    cannot prove is committed)."""
    d = _worktree_dir(session, lane_dirs)
    if not d or not os.path.isdir(d):
        return None, f"worktree dir unresolved ({d!r})"
    try:
        top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=15)
        if top.returncode != 0:
            return None, f"not a git worktree ({d})"
        root = top.stdout.strip()
        st = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=20)
        if st.returncode != 0:
            return None, f"git status failed in {root}"
        dirty = st.stdout.strip()
        if dirty:
            n = len(dirty.splitlines())
            return False, f"{n} uncommitted change(s) in {root}"
        return True, f"clean ({root})"
    except Exception as e:
        return None, f"git check errored: {e}"


_RESET_REANCHOR = (
    "You were WEDGED — idle with an unsubmitted next-step sitting in your composer, "
    "not progressing. Reconcile your agent_messages bus inbox and continue your task.")


def stage2_reset(session: str) -> tuple[bool, str]:
    """Stage 2 — escalate to scripts/reset_lane.sh with a generic re-anchor. The
    git-clean guard is the CALLER's responsibility (checked before we get here).
    reset_lane.sh itself refuses a BUSY lane and preserves the staged composer to a
    log. Returns (ran_ok, detail)."""
    try:
        r = subprocess.run(
            [str(_ORCH_DIR / "scripts" / "reset_lane.sh"), session, _RESET_REANCHOR],
            capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0
        tail = (r.stdout or r.stderr).strip().splitlines()
        return ok, (tail[-1] if tail else f"rc={r.returncode}")
    except Exception as e:
        return False, f"reset_lane.sh errored: {e}"


# ---------------------------------------------------------------------------
# Operator page (nazim-console) — the safety-alert path. Ungated by the lease.
# ---------------------------------------------------------------------------

def _page(text: str) -> None:
    """Deliver an operator alert on nazim-console. LANE_WEDGE_ALERT_STDOUT=1 (or
    pytest) prints instead of sending — so tests / manual inspection never page."""
    if os.environ.get("LANE_WEDGE_ALERT_STDOUT") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        print("[PAGE-would-send]\n" + text + "\n")
        return
    try:
        subprocess.run([str(_ORCH_DIR / "scripts" / "nazim_send.sh"), text],
                       timeout=30, cwd=str(_ORCH_DIR))
    except Exception as e:  # a page must never crash the loop
        print(f"[lane-wedge] page failed: {e}", file=sys.stderr)


def _wedge_alert(obs: Obs, elapsed_min: int, armed: bool) -> str:
    try:
        from nervous_system.alert_format import format_alert
        return format_alert(
            icon="⚠️",
            title=f"Lane '{obs.session}' looks wedged (idle, unsubmitted step)",
            what=(f"'{obs.session}' has been idle ~{elapsed_min} min with an unsubmitted "
                  f"step sitting in its composer and no new output — the idle-composer wedge."),
            why=("A wedged lane is alive but stops progressing and reads green on every "
                 "other gauge; on 2026-07-29 this silently froze two demo lanes for ~a day."),
            do=("It self-heals once its staged step is submitted." if not armed else
                "Auto-recovery is arming a verified submit; watch for confirmation."),
            detail=f"composer={obs.composer[:120]!r}; recovery {'ARMED' if armed else 'DRY-RUN (unarmed)'}.",
            ref="LANE-WEDGE-WATCHDOG",
        )
    except Exception:
        return (f"⚠️ Lane '{obs.session}' looks wedged — idle ~{elapsed_min} min with an "
                f"unsubmitted step in its composer. Staged: {obs.composer[:120]!r}. "
                f"{'Auto-recovery arming.' if armed else 'Recovery is unarmed (dry-run) — submit it or arm the watchdog.'}")


def _repeat_alert(session: str, n: int) -> str:
    try:
        from nervous_system.alert_format import format_alert
        return format_alert(
            icon="🚨",
            title=f"Lane '{session}' keeps re-wedging — auto-recovery stopped",
            what=(f"'{session}' has wedged {n} times in the repeat window; the wedge watchdog "
                  f"has STOPPED auto-acting on it."),
            why="A lane that re-wedges after recovery is a deeper problem than a dropped Enter.",
            do=f"Look at '{session}' by hand — its task, its boot state, or the harness may be stuck.",
            detail=f"repeat_k={REPEAT_K} window={REPEAT_WINDOW_SEC//3600}h",
            ref="LANE-WEDGE-WATCHDOG / REPEAT-WEDGE",
        )
    except Exception:
        return (f"🚨 Lane '{session}' has wedged {n}x in {REPEAT_WINDOW_SEC//3600}h — "
                f"auto-recovery STOPPED. Check it by hand.")


# ---------------------------------------------------------------------------
# Lane enumeration + worktree map
# ---------------------------------------------------------------------------

def list_lane_sessions() -> list[str]:
    """Live tmux sessions on the lane server, minus the excluded singletons. This
    is the authoritative 'what lanes are live' — the fleet_lanes registry drifts
    (it lists desired lanes, not the live set), so we enumerate the server the way
    reset_lane.sh/lane_nudge.sh operate: by tmux session name."""
    r = _tmux("list-sessions", "-F", "#{session_name}")
    if r is None or r.returncode != 0:
        return []
    out = []
    for name in r.stdout.splitlines():
        name = name.strip()
        if name and name not in EXCLUDE_SESSIONS:
            out.append(name)
    return out


def lane_worktree_map() -> dict[str, str]:
    """lane -> worktree_path from fleet_lanes (best-effort; empty on any DB error —
    the reset guard falls back to the pane's cwd)."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        return {}
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT lane, worktree_path FROM fleet_lanes WHERE worktree_path IS NOT NULL")
            return {lane: path for lane, path in cur.fetchall()}
    except Exception as e:
        log(f"lane_worktree_map: DB unavailable ({e}) — reset guard will use pane cwd")
        return {}


# ---------------------------------------------------------------------------
# Dead-man's-switch heartbeat
# ---------------------------------------------------------------------------

def _deadman_check_and_beat(state: dict, alert: bool) -> None:
    """Page (once) if the previous heartbeat is older than DEADMAN_GAP_SEC — the
    watchdog was down/stalled and just resumed. Then stamp a fresh heartbeat so an
    external monitor (fleet-health) can also see liveness. A silently-dead
    wedge-watchdog gives false confidence (feedback_monitors_need_deadmans_switch)."""
    now = time.time()
    last = 0.0
    try:
        last = float(HEARTBEAT_FILE.read_text().strip())
    except Exception:
        last = float(state.get("deadman", {}).get("last_beat", 0) or 0)
    gap = now - last
    if last and gap > DEADMAN_GAP_SEC:
        msg = (f"🐛 Lane-wedge watchdog was DOWN for ~{int(gap)//60} min "
               f"(gap {int(gap)}s > {DEADMAN_GAP_SEC}s) and just resumed — wedged lanes "
               f"could have gone undetected in that window. Check launchd "
               f"dev.wingmen.lane-wedge-watchdog.")
        log(msg)
        if alert:
            _page(msg)
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(str(now))
    except Exception:
        pass
    state.setdefault("deadman", {})["last_beat"] = now


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

@dataclass
class Cfg:
    dry: bool = True          # DRY-RUN default (ships this): no submit/reset
    alert: bool = False       # page operator on wedge/repeat/deadman
    lease_ok: bool = True     # set by the runner from fleet_health_lease.gate()


def run(dry: bool = True, alert: bool = False, as_json: bool = False,
        injected: Optional[list[Obs]] = None, lane_dirs: Optional[dict] = None,
        persist: Optional[bool] = None) -> int:
    """One scan. `injected` (list[Obs]) + lane_dirs are the test seam. persist
    defaults to (not dry) so a manual --dry-run never mutates production state
    with phantom episodes — EXCEPT we still want cross-scan episode tracking in
    dry mode for real classification, so dry persistence is opt-in via env or the
    demo harness (LANE_WEDGE_PERSIST_DRY=1)."""
    if persist is None:
        persist = (not dry) or os.environ.get("LANE_WEDGE_PERSIST_DRY") == "1"
    state = load_state()
    now = time.time()

    # Dead-man's-switch beats every scan (ungated — a safety signal is never gated).
    _deadman_check_and_beat(state, alert)

    # ACTIONS are lease-gated (CAI-RESP-501); detection + alerts are not. If a
    # different live body holds the pen, downgrade to dry (still classify + log +
    # alert) so the SRE and a reclaiming hub never both recover a lane.
    lease_ok, lease_why = (True, "dry-run (no actions)")
    if not dry:
        lease_ok, lease_why = fleet_health_lease.gate()
        if not lease_ok:
            log(f"pen-gate: recovery DEFERRED (CAI-RESP-501) — {lease_why}; "
                f"downgrading to dry-run (detect+log+alert only)")
            dry = True
            persist = False

    lane_dirs = lane_dirs if lane_dirs is not None else lane_worktree_map()
    sessions = injected if injected is not None else [observe(s) for s in list_lane_sessions()]

    results: list[dict] = []
    for obs in sessions:
        entry = state["lanes"].get(obs.session)
        verdict, entry = evaluate(entry, obs, now)
        state["lanes"][obs.session] = entry
        line = {"session": obs.session, "verdict": verdict, "working": obs.working,
                "composer": obs.composer, "reason": obs.reason}

        if verdict in (V_WORKING, V_IDLE_DONE, V_UNREACHABLE):
            results.append(line)
            continue

        elapsed_min = int((now - float(entry.get("first_seen", now))) / 60)
        line["polls"] = entry.get("poll_count")
        line["elapsed_min"] = elapsed_min

        if verdict == V_MONITORING:
            line["note"] = (f"staged, monitoring ({entry['poll_count']}/{WEDGE_MIN_POLLS} polls, "
                            f"{elapsed_min}m/{WEDGE_GRACE_SEC//60}m)")
            results.append(line)
            continue

        # verdict == V_WEDGE ----------------------------------------------------
        history = state["wedge_history"].setdefault(obs.session, [])
        recent = [t for t in history if now - t < REPEAT_WINDOW_SEC]

        # Repeat-wedge circuit breaker: stop auto-acting + page once.
        if len(recent) >= REPEAT_K:
            line["action"] = "REPEAT-WEDGE — auto-recovery STOPPED, paging operator"
            if not entry.get("repeat_alerted"):
                if alert:
                    _page(_repeat_alert(obs.session, len(recent)))
                entry["repeat_alerted"] = now
            log(f"REPEAT-WEDGE {obs.session}: {len(recent)} wedges in window — stop + page")
            results.append(line)
            continue

        # First crossing into wedge in this episode -> alert once (dry OR armed).
        if not entry.get("alerted"):
            if alert:
                _page(_wedge_alert(obs, elapsed_min, armed=not dry))
            entry["alerted"] = now
            log(f"WEDGE {obs.session}: idle {elapsed_min}m, staged={obs.composer[:80]!r} "
                f"({'ARMED' if not dry else 'DRY-RUN'})")

        if dry:
            line["action"] = f"[DRY] WOULD Stage-1 verified-submit '{obs.session}' (unarmed)"
            results.append(line)
            continue

        # ---- ARMED recovery ladder -------------------------------------------
        if not entry.get("stage1_at"):
            history.append(now)  # this wedge episode counts toward repeat detection
            ok, detail = stage1_submit(obs.session)
            entry["stage1_at"] = now
            line["action"] = f"Stage-1 submit '{obs.session}': {'RECOVERED' if ok else 'no working state'} — {detail}"
            log(line["action"])
            results.append(line)
            continue

        if now - float(entry["stage1_at"]) < STAGE2_DELAY_SEC:
            line["action"] = (f"Stage-1 submitted {int(now-entry['stage1_at'])}s ago — waiting "
                              f"{STAGE2_DELAY_SEC}s before Stage-2 reset")
            results.append(line)
            continue

        if entry.get("stage2_at"):
            line["action"] = f"Stage-2 already attempted at {int(now-entry['stage2_at'])}s ago — holding"
            results.append(line)
            continue

        # Stage 2 — guarded reset. NEVER reset over uncommitted work.
        clean, why = _worktree_clean(obs.session, lane_dirs)
        if clean is not True:
            line["action"] = f"Stage-2 SKIPPED — worktree not clean ({why}); alerting instead"
            if alert:
                _page(f"⚠️ Lane '{obs.session}' still wedged after a submit, but its git worktree "
                      f"is not clean ({why}) — NOT resetting (would risk uncommitted work). "
                      f"Please look at it by hand.")
            entry["stage2_at"] = now  # don't retry the reset every cycle
            log(line["action"])
            results.append(line)
            continue

        ok, detail = stage2_reset(obs.session)
        entry["stage2_at"] = now
        line["action"] = f"Stage-2 reset '{obs.session}': {'ran' if ok else 'FAILED'} — {detail} [{why}]"
        log(line["action"])
        results.append(line)

    if persist:
        save_state(state)

    # report
    if as_json:
        print(json.dumps({"dry": dry, "alert": alert, "lease": lease_why,
                          "results": results}, indent=2))
    else:
        mode = "DRY-RUN (detect+log only)" if dry else "ARMED (recovery live)"
        print(f"[lane-wedge] mode={mode} alert={'on' if alert else 'off'} "
              f"lanes={len(results)} lease={lease_why}")
        for line in results:
            v = line["verdict"]
            extra = line.get("note") or line.get("action") or line.get("reason") or ""
            print(f"  {v:11} {line['session']:14} {extra}")
    return 0


# ---------------------------------------------------------------------------
# Offline self-test — the strongest correctness evidence: proves the wedge state
# machine deterministically without touching a live lane. No DB, no tmux.
# ---------------------------------------------------------------------------

def self_test() -> int:
    import tempfile
    global STATE_FILE, LOG_FILE, HEARTBEAT_FILE, WEDGE_MIN_POLLS, WEDGE_GRACE_SEC
    failures: list[str] = []
    # Isolate every side-effect file to a throwaway dir — a test must NEVER write
    # fiction ("watchdog was DOWN 29,755,806 min") into the real audit log or touch
    # the real heartbeat (the ctx-watchdog 2026-07-26 test-pollution incident).
    _td = tempfile.mkdtemp(prefix="lane_wedge_selftest_")
    LOG_FILE = Path(_td) / "log"
    HEARTBEAT_FILE = Path(_td) / "hb"

    def check(cond, msg):
        if not cond:
            failures.append(msg)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    # deterministic thresholds for the test
    WEDGE_MIN_POLLS = 3
    WEDGE_GRACE_SEC = 120

    def O(sess, reachable=True, working=False, composer="", sig="S"):
        return Obs(sess, reachable=reachable, working=working, composer=composer, sig=sig)

    # DIM-AWARE COMPOSER EXTRACTION — the 2026-07-29 root-cause guard. These are
    # verbatim-shaped SGR captures from live wedged lanes (irsyad/cosem-video):
    # the "staged" text was DIM (SGR-2) ghost autosuggestion over an EMPTY buffer,
    # which the old plain-capture extractor misread as a staged step.
    ESC = "\x1b"
    NBSP = " "
    print("Dim (SGR-2) ghost autosuggestion reads as EMPTY (root-cause fix)")
    ghost = f"pretext\n{ESC}[39m❯{NBSP}{ESC}[2mfix the checked/banked labels while we wait{ESC}[0m"
    check(_extract_composer(ghost) == "", "fully-dim composer -> '' (ghost, not staged)")
    ghost_empty = f"{ESC}[38;5;246m❯{NBSP}{ESC}[39m"
    check(_extract_composer(ghost_empty) == "", "empty composer -> ''")
    real = f"pretext\n{ESC}[39m❯{NBSP}run the export now{ESC}[0m"
    check(_extract_composer(real) == "run the export now", "non-dim typed text -> real text")
    real_after_dim = f"{ESC}[39m❯{NBSP}{ESC}[2mghosthint{ESC}[0m actual typed text"
    check("actual typed text" in _extract_composer(real_after_dim),
          "dim run + real text present -> NOT swallowed as empty (fail-safe: monitor)")
    ph = f"{ESC}[39m❯{NBSP}{ESC}[2mTry \"how does foo work?\"{ESC}[0m"
    check(_extract_composer(ph) == "", "dim placeholder -> ''")

    t = 1000.0
    e = None

    print("Idle + EMPTY composer -> idle-done, never a wedge")
    v, e2 = evaluate(None, O("scholar", composer=""), t)
    check(v == V_IDLE_DONE, "empty composer classifies idle-done")

    print("Working lane -> working, never touched")
    v, e2 = evaluate(None, O("busy", working=True, composer=""), t)
    check(v == V_WORKING, "working lane classifies working")

    print("Staged composer: needs MIN_POLLS consecutive stable scans AND grace")
    e = None
    obs = O("exams", composer="run the export", sig="A")
    v, e = evaluate(e, obs, t);          check(v == V_MONITORING and e["poll_count"] == 1, "poll1 monitoring")
    v, e = evaluate(e, obs, t + 60);     check(v == V_MONITORING and e["poll_count"] == 2, "poll2 monitoring")
    # 3rd poll reaches MIN_POLLS but grace (120s) not yet met at t+119
    v, e = evaluate(e, obs, t + 119);    check(v == V_MONITORING, "poll3 but grace unmet -> still monitoring")
    v, e = evaluate(e, obs, t + 121);    check(v == V_WEDGE, "poll>=MIN and grace met -> WEDGE")

    print("A CHANGED composer resets the episode (never wedge a lane still typing)")
    e = None
    v, e = evaluate(e, O("x", composer="ru", sig="A"), t)
    v, e = evaluate(e, O("x", composer="run th", sig="B"), t + 60)
    check(e["poll_count"] == 1, "composer change restarts poll_count")
    v, e = evaluate(e, O("x", composer="run th", sig="B"), t + 300)
    check(v == V_MONITORING and e["poll_count"] == 2, "resumed accumulation after change")

    print("New pane OUTPUT (sig change) with same composer also resets (lane progressing)")
    e = None
    obs1 = O("y", composer="next step", sig="A")
    v, e = evaluate(e, obs1, t)
    v, e = evaluate(e, obs1, t + 60)
    v, e = evaluate(e, obs1, t + 130)
    check(v == V_WEDGE, "stable -> wedge")
    # now new output appears (sig changes) though composer same
    v, e = evaluate(e, O("y", composer="next step", sig="Z"), t + 190)
    check(v == V_MONITORING and e["poll_count"] == 1, "new output (sig change) restarts episode")

    print("Going WORKING mid-episode clears stage flags")
    e = {"sig": "A", "composer": "c", "first_seen": t, "poll_count": 9,
         "stage1_at": t, "alerted": t}
    v, e = evaluate(e, O("z", working=True), t + 10)
    check(v == V_WORKING and "stage1_at" not in e and "first_seen" not in e,
          "working verdict clears episode + stage flags")

    print("Unreachable -> unreachable, episode cleared")
    v, e = evaluate({"sig": "A", "poll_count": 5}, O("dead", reachable=False), t)
    check(v == V_UNREACHABLE and "poll_count" not in e, "unreachable clears episode")

    print("End-to-end via run(): DRY injected wedge -> WOULD-submit, no action, no state mutation")
    with tempfile.TemporaryDirectory() as td:
        STATE_FILE = Path(td) / "state.json"
        # seed a near-wedge episode so ONE more stable scan crosses the threshold
        seed = {"lanes": {"exams": {"sig": "A", "composer": "go", "first_seen": t - 999,
                                    "poll_count": WEDGE_MIN_POLLS - 1, "last_seen": t - 60}},
                "wedge_history": {}, "deadman": {"last_beat": t}}
        STATE_FILE.write_text(json.dumps(seed))
        os.environ["LANE_WEDGE_ALERT_STDOUT"] = "1"
        # inject the same stable observation; dry -> classify WEDGE, take no action
        rc = run(dry=True, alert=False, as_json=False,
                 injected=[Obs("exams", True, False, "go", "A")],
                 lane_dirs={}, persist=True)
        check(rc == 0, "run() returns 0 on a dry wedge scan")

    print()
    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} failure(s))")
        return 1
    print("SELF-TEST PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Lane idle-composer wedge watchdog (detect + guarded auto-recover).")
    ap.add_argument("--arm", action="store_true",
                    help="ARM the recovery ladder (Stage-1 submit -> Stage-2 reset), "
                         "lease-gated. Absent = DRY-RUN (detect + log only). Reviewer arms this.")
    ap.add_argument("--alert", action="store_true",
                    help="page the operator (nazim-console) on a wedge / repeat-wedge / watchdog-down gap")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--self-test", action="store_true",
                    help="offline state-machine checks (no DB, no tmux, no paging)")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return run(dry=not args.arm, alert=args.alert, as_json=args.json)


if __name__ == "__main__":
    # Dead-man's-switch: a watchdog that dies silently is worse than none. If
    # ANYTHING throws, page the operator via the dependency-free subprocess path
    # (does NOT import nervous_system) so the failure of the guard surfaces itself.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _e:
        import traceback
        traceback.print_exc()
        try:
            subprocess.run(
                [str(_ORCH_DIR / "scripts" / "nazim_send.sh"),
                 f"🐛 Lane-wedge watchdog CRASHED — it is NOT catching wedged lanes right now: {_e}. "
                 f"Fix before relying on it."],
                timeout=30, cwd=str(_ORCH_DIR))
        except Exception:
            pass
        sys.exit(1)
