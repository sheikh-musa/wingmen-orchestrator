"""Live tmux pane peek (read-only) — operator-requested "the lane cards look
stale" fix (agent_messages thread f869956c, msg 6156).

The DB-derived lane view (agent_status.current_task, last_heartbeat) is a
dumb 5-min timer + an almost-always-empty current_task — it shows a lane is
"up", never what it's actually doing. This module lets the console show the
lane's REAL tmux pane instead.

READ-ONLY BY CONSTRUCTION: the only tmux subcommands ever invoked are
`list-sessions` (to know what's real) and `capture-pane` (to read it) — never
send-keys or anything else. A session name is only ever passed to
`capture-pane` after it's been confirmed to be in the CURRENT, real
`list-sessions` output; an unrecognized/attacker-supplied name never reaches
the subprocess call. subprocess.run is always given an argv LIST (never
shell=True, never string-interpolated), so even an adversarial session name
can't break out of its single argv slot.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import List, Optional

_TIMEOUT_S = 5
_RAW_CAPTURE_LINES = 200  # generous raw window BEFORE chrome-filtering — filter
                          # first then cap to _CAPTURE_LINES, not the reverse,
                          # or a noise-dominated tail leaves little real content
_CAPTURE_LINES = 60       # shown to the operator, after filtering (msg re: UX fixes)

# Claude-Code-TUI chrome to strip so what's left reads as actual activity, not
# boot banner clutter (operator feedback: a low-activity/freshly-booted lane's
# boot banner genuinely hasn't scrolled out of the pane yet — real content,
# but unreadable buried in box-drawing + footer noise).
_BOX_CHARS = set("─═│║╔╗╚╝╭╮╰╯┌┐└┘├┤┬┴┼")
_FOOTER_RE = re.compile(r"bypass permissions|esc to interrupt|shift\+tab to cycle|for agents")
_CLEAR_HINT_RE = re.compile(r"new task\?.*token", re.IGNORECASE)
_BARE_PROMPT_RE = re.compile(r"^\s*❯\s*$")
# extra noise to drop so the peek reads like an activity log, not a terminal
# (operator: "more human-readable peeks", 2026-07-08): TUI hint lines + the
# sub-agent "◯ Explore … N tokens" status rows that are pure churn telemetry.
_NOISE_RE = re.compile(r"Press up to edit queued|↑ to manage|ctrl\+t to|◯\s+Explore\b", re.IGNORECASE)
# leading tool/spinner glyphs to peel off each kept line's front so text starts
# with actual content, and a trailing "(Ns · ↓Nk tokens · thinking)" telemetry
# suffix on spinner lines that adds nothing human-readable.
_LEADING_GLYPHS = "⎿●◯○✻✳✶✷✸✹✺✽❋⚹⏵⎾⌐▶ ·\t"
_SPINNER_TELEMETRY_RE = re.compile(r"\s*[(·]\s*\d+m?\s*\d*s?\b.*?(tokens|thinking|thought for).*$", re.IGNORECASE)


def _is_chrome(line: str) -> bool:
    """True for a line that's TUI decoration, not activity: pure box-drawing/
    separator lines, the bypass-permissions/esc-to-interrupt footer, the
    /clear-to-save-tokens hint, a bare empty prompt, or blank lines. A prompt
    line WITH typed content, or banner text alongside a border character, is
    NOT stripped — only lines that are mostly/purely decorative."""
    stripped = line.strip()
    if not stripped:
        return True
    if _BARE_PROMPT_RE.match(line):
        return True
    if _FOOTER_RE.search(line):
        return True
    if _CLEAR_HINT_RE.search(line):
        return True
    if _NOISE_RE.search(line):
        return True
    non_space = [c for c in stripped if not c.isspace()]
    if non_space and sum(1 for c in non_space if c in _BOX_CHARS) / len(non_space) > 0.6:
        return True
    return False


def _clean_pane_text(raw: str) -> str:
    kept = []
    for ln in raw.splitlines():
        if _is_chrome(ln):
            continue
        # peel leading tool/spinner glyphs + drop the trailing token-telemetry
        # so each line reads as plain activity, not terminal chrome.
        s = ln.rstrip().lstrip(_LEADING_GLYPHS)
        s = _SPINNER_TELEMETRY_RE.sub("", s).rstrip()
        if s:
            kept.append(s)
    return "\n".join(kept[-_CAPTURE_LINES:])


def _tmux_bin() -> str:
    """launchd runs this process with a minimal PATH (/usr/bin:/bin:/usr/sbin
    :/sbin) that does NOT include /opt/homebrew/bin — a bare "tmux" silently
    fails there (caught by the broad except below) and live_sessions() always
    returns [], making every session look "not live" even though real ones
    exist. Same class of bug _resolve_host already works around for
    tailscale; same fix here: try known absolute paths first."""
    for candidate in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "tmux"  # PATH-relative fallback for interactive/dev shells


_TMUX = _tmux_bin()


def live_sessions() -> List[str]:
    """Real, currently-live tmux session names. Never trust client input for
    this — it's the sole source of truth for what capture_pane is allowed to
    read."""
    try:
        r = subprocess.run(
            [_TMUX, "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode != 0:
            return []
        return [s for s in r.stdout.splitlines() if s]
    except Exception:
        return []


def capture_pane(session: str) -> Optional[str]:
    """Return the last ~60 lines of *session*'s pane 0 AFTER stripping TUI
    chrome, or None if the session isn't live right now (checked against
    live_sessions(), not trusted from the caller) or the capture otherwise
    fails.

    Captures a generous raw window (-S -200, clamped automatically by tmux to
    whatever's actually available — never an error, never padded) and filters
    BEFORE capping to _CAPTURE_LINES: filtering first then capping means a
    chrome-dominated tail still leaves a full _CAPTURE_LINES of real content
    where available, instead of capping first and filtering a narrow raw
    window down to almost nothing. The `=session:0.0` target is tmux's
    EXACT-match session selector (never substring/prefix) — defense in depth
    on top of the live_sessions() membership check, since by the time this
    runs the name has already been confirmed to be a real, live session.
    """
    if not session or session not in live_sessions():
        return None
    try:
        r = subprocess.run(
            [_TMUX, "capture-pane", "-t", f"={session}:0.0", "-p", "-S", f"-{_RAW_CAPTURE_LINES}"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode != 0:
            return None
        return _clean_pane_text(r.stdout)
    except Exception:
        return None
