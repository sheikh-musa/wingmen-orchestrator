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
import threading
import time
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

# Wrap-continuation REFLOW (operator #3729): the Claude-Code TUI word-wraps its
# output and draws each wrapped row as its OWN physical line, so one sentence
# arrives split across 2-3 lines and the peek reads as broken mid-sentence prose.
# Because the TUI word-wraps (breaks at spaces, so a wrapped line ends SHORT of
# the pane width, not exactly at it) a "line fills the width" test can't tell a
# wrap from a real newline — so we reflow the Markdown way: collapse the soft
# newlines WITHIN a run of consecutive prose lines into spaces, and break only on
# real boundaries. Boundaries = chrome/blank lines AND structure-starts (a line
# beginning with a bullet, task/tool glyph, or "N." marker) — so intentional
# lists + blank-separated paragraphs stay separate instead of smearing into one
# blob, while a wrapped sentence rejoins into flowing prose.
# structure-start chars: bullets/task-glyphs/tool-glyphs Claude draws each on
# their own line — a line beginning with one starts a NEW logical line.
_STRUCTURE_CHARS = set("⎿●◯○✻✳✶✷✸✹✺✽❋⚹⏵▶→◼◻▪✔✅✓✗✘☐☑-*•◦‣❯")
_NUM_LIST_RE = re.compile(r"^[0-9]+[.)]\s")


def _starts_structure(stripped: str) -> bool:
    """True if a (whitespace-stripped) content line begins a new list item /
    bulleted or tool row — a hard boundary the reflow must NOT join onto the
    previous line, so lists stay lists."""
    if not stripped:
        return False
    return stripped[0] in _STRUCTURE_CHARS or bool(_NUM_LIST_RE.match(stripped))


def _reflow(raw: str):
    """Join soft-wrapped continuation lines into flowing logical lines. Returns a
    list of logical lines: chrome dropped, consecutive prose rejoined with a
    single space, lists + blank-separated paragraphs kept as their own lines."""
    out = []
    buf = None
    for ln in raw.splitlines():
        if _is_chrome(ln):                       # box/footer/blank/bare-prompt/noise = boundary
            if buf is not None:
                out.append(buf)
                buf = None
            continue
        content = ln.rstrip()
        stripped = content.lstrip()
        if buf is not None and _starts_structure(stripped):
            out.append(buf)                       # a new bullet/list item = its own logical line
            buf = None
        if buf is None:
            buf = content
        else:
            buf = buf + " " + stripped            # soft-wrap continuation: rejoin with a single space
    if buf is not None:
        out.append(buf)
    return out


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
    """Chrome-strip + REFLOW the raw pane into readable prose: rejoin soft-wrapped
    lines into flowing logical lines, then peel each logical line's leading
    tool/spinner glyphs and drop its trailing token-telemetry."""
    kept = []
    for s in _reflow(raw):
        s = s.lstrip(_LEADING_GLYPHS)
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


# Spinner glyphs Claude-Code shows on its ACTIVE status line. Used only for
# working-detection on RAW text (below); the cleaner peels these off, which is
# exactly why working-detection can't run on cleaned text.
_SPINNER_GLYPHS = "✻✳✶✷✸✹✺✽❋⚹"


def _raw_capture(session: str) -> Optional[str]:
    """The unfiltered pane text (last _RAW_CAPTURE_LINES). Callers do the
    live-session membership check first; this is just the subprocess."""
    try:
        r = subprocess.run(
            [_TMUX, "capture-pane", "-t", f"={session}:0.0", "-p", "-S", f"-{_RAW_CAPTURE_LINES}"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _is_working(raw: str) -> bool:
    """True if the RAW pane shows Claude actively generating a turn. The two
    reliable signals are the 'esc to interrupt' footer (present ONLY while a
    turn runs) and an active spinner status line ('✻ … N tokens / thinking').
    Both are TUI chrome that _clean_pane_text STRIPS — so working-detection
    MUST read raw text, not the cleaned peek text. Reading cleaned text is the
    '0 working / every lane idle' bug (2026-07-11)."""
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    # Primary, reliable signal: the footer is shown ONLY while a turn runs.
    if any("esc to interrupt" in ln.lower() for ln in lines):
        return True
    # Secondary: an ACTIVE spinner status line. Match only genuinely-in-progress
    # markers — the '…' ellipsis of a running verb ('Bloviating…'), a live
    # up/down token counter, or present-tense 'thinking'. Deliberately NOT the
    # past-tense DONE summaries the same glyph also draws between turns
    # ('✻ Cooked for 4m', 'Baked for 3m', 'Thought for 5s'), which would
    # otherwise mis-read an idle-at-prompt lane as working.
    spinner = next((ln for ln in reversed(lines) if ln.lstrip()[:1] in _SPINNER_GLYPHS), None)
    if spinner:
        if "…" in spinner or "↑" in spinner or "↓" in spinner or "thinking" in spinner.lower():
            return True
    return False


def capture(session: str, live: Optional[set] = None):
    """One raw capture, BOTH derivations: returns (state, cleaned_text) or
    (None, None) if the session isn't live. state = {running, state:
    'working'|'idle', activity}. `live` lets the caller pass a pre-fetched
    live-session set so an N-lane fleet sweep does one `list-sessions`, not N.

    working/idle comes from the RAW pane (_is_working); the human-readable
    `activity` and the peek text come from the cleaned pane — the split is why
    a lane can read 'working' while its card still shows a readable activity
    line."""
    sessions = live if live is not None else set(live_sessions())
    if not session or session not in sessions:
        return None, None
    raw = _raw_capture(session)
    if raw is None:
        return None, None
    cleaned = _clean_pane_text(raw)
    activity = cleaned.splitlines()[-1][:140] if cleaned else ""
    state = {
        "running": True,
        "state": "working" if _is_working(raw) else "idle",
        "activity": activity,
    }
    return state, cleaned


# --- cross-host coordinator panes (operator #3729) --------------------------
# Some coordinator bodies run on a DIFFERENT machine than the console host —
# Nazim's 'nazim' tmux lives on the Mini, not the Studio. To peek those, the
# console SSHes to the host and runs the SAME read-only capture-pane there.
#
# SECURITY: host / user / tmux-path / target below are FIXED constants — NEVER
# derived from client input. The peek endpoint's session name is only ever used
# as a dict KEY lookup here; an unknown/crafted name finds no entry and gets no
# capture. The remote command is a fixed argv (capture-pane -p, read-only), run
# over BatchMode ssh (fails instead of ever prompting) with hard connect + overall
# timeouts, so an asleep/unreachable host fails CLOSED — the peek degrades to the
# card's bus-activity view and never hangs the fleet payload.
class _RemotePane:
    __slots__ = ("host", "tmux", "target")

    def __init__(self, host: str, tmux: str, target: str):
        self.host = host
        self.tmux = tmux
        self.target = target


# DESIGN/SECURITY GATE (operator #3729): cross-host peek gives the console
# OUTBOUND SSH — an attack-surface expansion the operator has HELD for a security
# nod (least-privilege: dedicated key / command allowlist / no shell / maybe cai).
# So it is DEFAULT-OFF and fully inert (no ssh ever fires) unless the console's
# env explicitly opts in with CONSOLE_CROSS_HOST_PEEK=1. This is also the belt
# against a launchd respawn silently activating it: a respawn onto this HEAD is
# safe because the flag defaults off. Flip the env var (not the code) to enable
# once the review clears.
_CROSS_HOST_ENABLED = os.environ.get("CONSOLE_CROSS_HOST_PEEK", "").strip().lower() in ("1", "true", "yes", "on")

_REMOTE_PANES = {
    # Nazim (2nd coordinator) runs on the Mini under sheikhmusa via the Intel
    # tmux path; reachable from the Studio over Tailscale SSH (key auth).
    "nazim": _RemotePane("Musa@100.83.21.34", "/usr/local/bin/tmux", "=nazim:0.0"),
}
_SSH = "/usr/bin/ssh"
_REMOTE_TIMEOUT_S = 8          # overall subprocess cap (ssh ConnectTimeout is 5)
_REACH_TTL_S = 45             # how long a reachability-probe result is trusted
_reach_cache = {}            # session -> {"ok": bool, "ts": float}
_reach_lock = threading.Lock()
_reach_inflight = set()      # sessions with a probe currently running (dedupe)


def _remote_raw_capture(spec: "_RemotePane") -> Optional[str]:
    """Read-only capture-pane over ssh with a FIXED argv. Fails closed (None) on
    any error/timeout — never raises, never hangs past _REMOTE_TIMEOUT_S."""
    try:
        # ssh runs the remote command through the login shell (zsh on the Mini),
        # so the target MUST be single-quoted or zsh's `=`-expansion mangles the
        # `=nazim:0.0` exact-match selector ("zsh: nazim:0.0 not found"). All
        # fields are FIXED constants (no client input), so quoting is safe.
        remote_cmd = f"{spec.tmux} capture-pane -t '{spec.target}' -p -S -{_RAW_CAPTURE_LINES}"
        r = subprocess.run(
            [_SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new", spec.host, remote_cmd],
            capture_output=True, text=True, timeout=_REMOTE_TIMEOUT_S,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _stamp_reach(session: str, ok: bool) -> None:
    with _reach_lock:
        _reach_cache[session] = {"ok": ok, "ts": time.time()}
        _reach_inflight.discard(session)


def _refresh_reach(session: str, spec: "_RemotePane") -> None:
    _stamp_reach(session, _remote_raw_capture(spec) is not None)


def remote_peekable(session: str) -> bool:
    """Whether a cross-host coordinator pane is currently reachable. NON-BLOCKING:
    returns the cached probe result; on a stale/missing cache it kicks a
    background probe and returns the last-known value (optimistic True on first
    sight) so the fleet payload never waits on ssh. The card's peek arrow tracks
    this, so an unreachable host shows NO arrow (keeps its bus-activity view)."""
    if not _CROSS_HOST_ENABLED:
        return False                          # capability held (default-off) — no ssh, no arrow
    spec = _REMOTE_PANES.get(session)
    if spec is None:
        return False
    now = time.time()
    with _reach_lock:
        entry = _reach_cache.get(session)
        if entry is not None and (now - entry["ts"]) < _REACH_TTL_S:
            return entry["ok"]
        if session not in _reach_inflight:
            _reach_inflight.add(session)
            threading.Thread(target=_refresh_reach, args=(session, spec), daemon=True).start()
        return entry["ok"] if entry is not None else True   # optimistic first paint


def capture_pane(session: str) -> Optional[str]:
    """Return the last ~60 lines of *session*'s pane 0 AFTER stripping TUI
    chrome + reflowing, or None if the session isn't a live local session, isn't
    a known cross-host coordinator pane, or the capture otherwise fails.

    Captures a generous raw window (-S -200, clamped automatically by tmux to
    whatever's actually available — never an error, never padded) and filters
    BEFORE capping to _CAPTURE_LINES: filtering first then capping means a
    chrome-dominated tail still leaves a full _CAPTURE_LINES of real content
    where available, instead of capping first and filtering a narrow raw
    window down to almost nothing. The `=session:0.0` target is tmux's
    EXACT-match session selector (never substring/prefix) — defense in depth
    on top of the live_sessions() membership check, since by the time this
    runs the name has already been confirmed to be a real, live session.

    A session that isn't local-live is checked against the FIXED cross-host
    registry (_REMOTE_PANES) — an unknown name never reaches any subprocess.
    """
    if session and session in live_sessions():
        raw = _raw_capture(session)
        return _clean_pane_text(raw) if raw is not None else None
    # not local: a known cross-host coordinator pane (e.g. Nazim on the Mini)?
    # Gated OFF by default — no ssh unless the console opts in (security hold).
    spec = _REMOTE_PANES.get(session) if _CROSS_HOST_ENABLED else None
    if spec is not None:
        raw = _remote_raw_capture(spec)
        _stamp_reach(session, raw is not None)   # a real peek is a fresh reachability signal
        return _clean_pane_text(raw) if raw is not None else None
    return None
