#!/usr/bin/env python3
"""pane_bloat_signal — the FRESH, per-pane context-bloat gauge for worker lanes.

WHY THIS EXISTS (op#13050 root cause, verified 2026-08-15 by cc-fleet-health):
the cc_session_costs DB gauge that context_health_watchdog / auto_recycle_on_bloat
read is UNRELIABLE for worker lanes in TWO independent ways:
  1. STALE-ON-IDLE: the cost writer only updates on a turn, so an IDLE-but-bloated
     lane's gauge freezes. prog1 sat at 100% with an 84h-stale gauge -> invisible to
     the gauge-based detector (the whole reason auto-recycle "didn't fire again").
  2. MIS-MAPPED / LIVE-WRONG: worker sub-tag instances collapse onto their base row,
     and the base row can be flat-out wrong. Measured live on 2026-08-15:
       cc-irsyad  gauge  8%  <->  pane "/clear to save 795.9k tokens" (~80%)
       shipforge  gauge 13%  <->  pane "/clear to save 652.4k tokens" (~65%)
     Where the gauge was FRESH it matched the pane (caai 55%<->551k, cai 44%<->437k),
     so the pane is the ground truth and the gauge lies when stale/mis-mapped.

THE SIGNAL: Claude Code renders its own reclaim hint above the composer once context
is substantial:  `new task? /clear to save {N}k tokens`  — where N is the reclaimable
context in thousands of tokens. It is LIVE (read straight off the pane), PER-SESSION
(no base-vs-instance confusion), and present exactly when a lane is at/near an idle
composer with meaningful context (empirically from ~440k up). CAVEAT (op#13050,
verified 2026-08-15): the hint is NOT reliably present at high fill — it VANISHES in a
BLIND BAND below the ~95% pct-line cliff (observed ABSENT at 939.9k/94%: the `/clear`
hint already gone, the `{N}% context used` line not yet shown; the lower edge is
UNMEASURED — hint still seen at 79%). So an ABSENT hint means one of THREE things:
context below CC's nudge bar, mid-turn, OR blind-band-bloated-but-invisible — it does
NOT prove NOT-bloated. Fail-CLOSED keeps this safe for the FIRE path (a missing/failed
signal is NEVER a fire), but callers must NOT read absent-hint as proof of low context:
the blind band is a DETECTION gap, covered by the DB gauge / session-identity resolver
(context_truth), not by this pane signal alone.

This module is READ-ONLY (tmux capture-pane). It fires nothing and changes no state.
"""
from __future__ import annotations

import re
import subprocess

# `new task? /clear to save 795.9k tokens`  ->  795.9   (also "551k", "454k")
_HINT_RX = re.compile(r"/clear to save\s+([0-9]+(?:\.[0-9]+)?)\s*k\s+tokens")

# `100% context used`, `98% context used`  ->  100, 98. THE CLIFF SIGNAL (op#13186):
# once context is very high (empirically >=~95%) Claude Code STOPS rendering the
# `/clear to save {N}k` reclaim hint and instead prints `{N}% context used`. So a lane
# AT the cliff shows NO hint -> _HINT_RX misses -> the MOST-bloated lane read as
# not-bloated (fail-closed miss; cc-ihsanos-1 @100% was invisible). This regex reads
# that pct line directly; it is the authoritative truth near the cliff.
_PCT_RX = re.compile(r"([0-9]+)%\s+context used")

# Mini lanes live on the /usr/local/bin/tmux server (socket tmux-501/default), NOT
# /opt/homebrew/bin/tmux — same note as reset_lane.sh / composer_capture.sh.
DEFAULT_TMUX = "/usr/local/bin/tmux"


def parse_hint_k(pane_text: "str | None") -> "float | None":
    """PURE. Reclaimable-context in K-tokens (float) parsed from a pane's text, or
    None if CC's `/clear to save {N}k tokens` hint is absent. Takes the LAST match —
    the pane can carry an older hint up in scrollback; the freshest render wins."""
    if not pane_text:
        return None
    matches = _HINT_RX.findall(pane_text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


def parse_pct(pane_text: "str | None") -> "int | None":
    """PURE. Current-context percent (int, 0-100) parsed from a pane's `{N}% context
    used` line, or None when that line is absent. Takes the LAST match — same freshest-
    render-wins rule as parse_hint_k (an older render can linger up in scrollback).
    This is the authoritative near-the-cliff signal (op#13186): CC shows it exactly when
    context is high enough that it has stopped rendering the /clear reclaim hint."""
    if not pane_text:
        return None
    matches = _PCT_RX.findall(pane_text)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except (TypeError, ValueError):
        return None


def capture_pane(session: str, tmux: str = DEFAULT_TMUX) -> "str | None":
    """READ-ONLY tmux capture of `session`'s active pane. Returns the visible text,
    or None on any failure (missing session, tmux error, timeout) -> fail-closed."""
    try:
        r = subprocess.run(
            [tmux, "capture-pane", "-p", "-t", session],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001 — any capture failure is fail-closed
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def pane_bloat_k(session: str, tmux: str = DEFAULT_TMUX) -> "float | None":
    """Fresh reclaimable-context (K-tokens) for a LIVE session's pane, or None if the
    hint is absent / the capture failed. None ALWAYS means 'do not treat as bloated'
    (fail-closed) — never fire on a signal we could not read."""
    return parse_hint_k(capture_pane(session, tmux))


def pane_context_pct(session: str, tmux: str = DEFAULT_TMUX) -> "int | None":
    """Fresh current-context percent (int 0-100) for a LIVE session's pane from CC's
    `{N}% context used` line, or None if that line is absent / the capture failed. None
    ALWAYS means 'no pct signal' (fail-closed) — never a fake 0/green. This is the truth
    at/near the cliff where pane_bloat_k goes blind (the /clear hint has vanished)."""
    return parse_pct(capture_pane(session, tmux))


def is_bloated_k(k: "float | None", fire_k: float) -> bool:
    """PURE. True only when a fresh pane read `k` is at/above the fire bar `fire_k`
    (both in K-tokens). None (no hint / capture failed) => False (fail-closed)."""
    if k is None:
        return False
    return k >= fire_k
