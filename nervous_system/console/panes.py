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

import hashlib
import json
import os
import re
import subprocess
import time
from typing import Dict, List, Optional

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


def capture_pane(session: str) -> Optional[str]:
    """Return the last ~60 lines of *session*'s pane 0 AFTER stripping TUI
    chrome + reflowing, or None if the session isn't live right now (checked
    against live_sessions(), not trusted from the caller) or the capture fails.

    LOCAL tmux only — the console never reaches off-box. A cross-host coordinator
    (Nazim on the Mini) is surfaced via a DB read of its bus activity instead
    (db.fetch_coordinator_peek), NOT ssh, keeping the console's surface local
    read-only (operator #3729: reverted the ssh peek to a DB-read replacement).

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
    raw = _raw_capture(session)
    return _clean_pane_text(raw) if raw is not None else None


# --- token / auth per lane (Max vs metered, + which Max account) --------------
#
# Each fleet lane is a `claude` CLI process on THIS console host (local-only,
# same surface as the tmux peek — never reaches off-box). Two signals per lane:
#
#   * BILLING: a NON-EMPTY ANTHROPIC_API_KEY in the process env => metered API
#     (red). Absent OR present-but-empty (the launchers scrub it to run on the
#     Max subscription) => Max (green). Empty-but-present is why a bare "is the
#     var set" test is WRONG — orch runs with ANTHROPIC_API_KEY= (scrubbed).
#   * ACCOUNT: a FINGERPRINT of CLAUDE_CODE_OAUTH_TOKEN (sha256 prefix — the raw
#     token NEVER leaves this function, is never returned, logged, or displayed)
#     mapped to an owner label. The console host's OWN .env token (os.environ)
#     is the operator's, auto-labelled 'operator'; other fingerprints are
#     labelled via CONSOLE_MAX_ACCT_OWNERS (JSON {fp: label}) or, absent that,
#     shown as 'Max (unknown acct)' — never nothing.
#
# READ-ONLY: only `ps` + tmux `list-panes` are invoked, argv lists, no shell.
_UNKNOWN_ACCT = "Max (unknown acct)"
_METERED = "metered"


def _fingerprint(token: str) -> str:
    """A stable, NON-reversible short id for an OAuth token. sha256 prefix — the
    raw token is never surfaced anywhere. 12 hex chars to match the operator's
    canonical account fingerprints (musa=68142948c003, syed=582043088eae, op#10706)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _owner_map() -> Dict[str, str]:
    """fingerprint -> owner label. The console host's own .env OAuth token
    (already loaded into os.environ by __main__) is the operator's, so its
    fingerprint auto-maps to 'operator' — no config needed for the common case.
    CONSOLE_MAX_ACCT_OWNERS (JSON) adds/overrides other accounts (e.g. Syed)."""
    m: Dict[str, str] = {}
    own = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if own:
        m[_fingerprint(own)] = "operator"
    raw = os.environ.get("CONSOLE_MAX_ACCT_OWNERS")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                # explicit config wins over the auto-derived operator label
                m.update({str(k): str(v) for k, v in extra.items()})
        except Exception:
            pass
    return m


def _pid_to_session() -> Dict[int, str]:
    """Map every live tmux pane's process pid -> its session name, so a claude
    pid can be walked up its parent chain to the owning lane."""
    out: Dict[int, str] = {}
    try:
        r = subprocess.run(
            [_TMUX, "list-panes", "-a", "-F", "#{session_name} #{pane_pid}"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode != 0:
            return out
        for ln in r.stdout.splitlines():
            parts = ln.split()
            if len(parts) == 2 and parts[1].isdigit():
                out[int(parts[1])] = parts[0]
    except Exception:
        pass
    return out


def _ppid_map() -> Dict[int, int]:
    """pid -> ppid for every process (so a claude pid can be walked up to a tmux
    pane pid to resolve its lane)."""
    out: Dict[int, int] = {}
    try:
        r = subprocess.run(
            ["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        for ln in r.stdout.splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                out[int(parts[0])] = int(parts[1])
    except Exception:
        pass
    return out


def _session_for(pid: int, pane_pids: Dict[int, str], ppid: Dict[int, int]) -> Optional[str]:
    cur, seen = pid, 0
    while cur and cur > 1 and seen < 40:
        if cur in pane_pids:
            return pane_pids[cur]
        cur = ppid.get(cur)
        seen += 1
    return None


def lane_token_auth() -> dict:
    """Per-lane billing (Max vs metered) + Max-account owner, with a fleet
    summary. Local `ps eww` on the console host; the raw OAuth token is fingerprinted
    in-process and never returned. Best-effort: any failure returns an empty set
    rather than raising into the fleet aggregate.

    Returns {"lanes": [{session, metered, acct, owner}], "summary": {...}}."""
    owner_map = _owner_map()
    pane_pids = _pid_to_session()
    ppid = _ppid_map()
    lanes: List[dict] = []
    try:
        r = subprocess.run(
            ["ps", "eww", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        eww = r.stdout
    except Exception:
        eww = ""

    for ln in eww.splitlines():
        low = ln.lower()
        # A fleet lane = the claude CLI booted with --dangerously-skip-permissions.
        if "claude" not in low or "--dangerously-skip-permissions" not in ln:
            continue
        m = re.match(r"\s*(\d+)\s", ln)
        if not m:
            continue
        pid = int(m.group(1))
        # NON-EMPTY ANTHROPIC_API_KEY = metered (empty/absent = scrubbed => Max).
        km = re.search(r"ANTHROPIC_API_KEY=(\S*)", ln)
        metered = bool(km and km.group(1))
        tok = re.search(r"CLAUDE_CODE_OAUTH_TOKEN=(\S+)", ln)
        fp = _fingerprint(tok.group(1)) if tok else None
        if metered:
            owner = _METERED
        elif fp is not None:
            owner = owner_map.get(fp, _UNKNOWN_ACCT)
        else:
            owner = _UNKNOWN_ACCT
        session = _session_for(pid, pane_pids, ppid) or ("pid:%d" % pid)
        lanes.append({"session": session, "metered": metered, "acct": fp, "owner": owner})

    lanes.sort(key=lambda x: (x["metered"] is False, x["owner"], x["session"]))
    by_owner: Dict[str, int] = {}
    metered_n = 0
    for l in lanes:
        if l["metered"]:
            metered_n += 1
        else:
            by_owner[l["owner"]] = by_owner.get(l["owner"], 0) + 1
    return {
        "lanes": lanes,
        "summary": {"by_owner": by_owner, "metered": metered_n, "total": len(lanes)},
    }


# ── GROUND-TRUTH token page (op#10706 / op#10715) ────────────────────────────
# The operator LOST TRUST in declared token values (the console reported Musa
# while ACTUALLY on Syed). This scan reports ONLY the process-verified account —
# read from the live claude proc's env — never a declared/agent_status/config
# value. Anything that can't be process-verified is UNVERIFIED, never a guess.
#
# Known Max accounts by token fingerprint (sha256[:12] — NON-secret short ids the
# operator already uses on the bus, never the raw token). CONSOLE_MAX_ACCT_OWNERS
# (JSON {fp: label}) extends/overrides. Keyed at the length _fingerprint() emits.
_KNOWN_ACCOUNTS = {"68142948c003": "Musa", "582043088eae": "Syed"}
# FINAL fallback label only (op#10706 R1 replaced the flat policy): when a body's
# expected account can't be resolved from its pointer file NOR the .env default,
# this is the last-resort expected label.
_EXPECTED_ACCOUNT = os.environ.get("CONSOLE_EXPECTED_ACCOUNT", "Musa")
# Bodies the operator wants proven that do NOT run on THIS host: the hub
# (cc-orchestrator) lives on the VPS, so a Mini-local `ps` cannot read its token.
# Reported UNVERIFIED (remote) rather than guessed (op#10715). {session: host}.
_REMOTE_BODIES = {"cc-orchestrator": "VPS"}

# ── Expected-account per body from its POINTER FILE (op#10706 R1) ─────────────
# orch-console ruling (#16038): a body's EXPECTED account = whatever its per-body
# token POINTER FILE resolves to (the token file it points at), NOT a hardcoded
# account. mismatch = live-proc fp != pointer-resolved fp — so the RED flag means
# "this body is NOT on its OWN configured default" (self-consistent + reversible).
_ORCH_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Which pointer file governs each body. Console/hub read their own; every worker
# lane reads .lane_default_token. The Mini singletons cai + the SRE boot straight
# off .env (no pointer) -> their expected = the .env default account.
_BODY_POINTER = {"nazim": ".nazim_default_token", "cc-orchestrator": ".orch_default_token"}
_NO_POINTER_SINGLETONS = {"cai", "fleet-health"}


def _read_token_fp(path: str) -> Optional[str]:
    """Fingerprint the token in a token file (raw token stays local, never
    returned). None on any read failure."""
    try:
        with open(os.path.expanduser(path.strip()), "r") as f:
            tok = f.read().strip()
        return _fingerprint(tok) if tok else None
    except Exception:
        return None


def _env_default_fp() -> Optional[str]:
    own = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    return _fingerprint(own) if own else None


def _expected_fp(session: str) -> Optional[str]:
    """The account fingerprint a body is CONFIGURED to run on: its pointer file's
    target token, else the .env default account. None only if nothing resolves."""
    ptr = _BODY_POINTER.get(session)
    if ptr is None and session not in _NO_POINTER_SINGLETONS:
        ptr = ".lane_default_token"          # a worker lane
    if ptr:
        try:
            with open(os.path.join(_ORCH_DIR, ptr), "r") as f:
                tokfile = f.read().strip()
            if tokfile:
                fp = _read_token_fp(tokfile)
                if fp:
                    return fp
        except Exception:
            pass
    return _env_default_fp()                  # no/unreadable pointer -> fleet default


def _account_labels() -> Dict[str, str]:
    labels = dict(_KNOWN_ACCOUNTS)
    raw = os.environ.get("CONSOLE_MAX_ACCT_OWNERS")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                labels.update({str(k): str(v) for k, v in extra.items()})
        except Exception:
            pass
    return labels


# ── Remote hub scan (op#10706 C) — source of truth REGARDLESS of host ─────────
# The hub (cc-orchestrator) runs on the VPS, so a Mini-local ps can't read it.
# The operator wants the tool to be source-of-truth wherever a body runs, so we
# SSH in and fingerprint the hub's token REMOTE-SIDE (raw token NEVER leaves the
# VPS — sha256 on the remote box, only the fp returns). Read-only (ps only).
# Cached (TTL) so /api/token-truth polls don't re-SSH each time; slow/failed SSH
# => UNVERIFIED (never an error into the aggregate, never a guess).
_REMOTE_HUB_HOST = os.environ.get("CONSOLE_HUB_SSH", "root@91.107.235.77")
_REMOTE_HUB_KEY = os.path.expanduser(os.environ.get("CONSOLE_HUB_SSH_KEY", "~/.ssh/wingmen_vps"))
_REMOTE_CACHE_TTL_S = 45.0
_remote_hub_cache = {"at": 0.0, "val": None}  # val = {"fp","model"} | None
# Linux box: sha256sum (not shasum). Fingerprint the token remote-side; print only
# "<fp> <model>" — the raw token is read into $tok and never echoed.
_REMOTE_SCAN_SH = (
    "pid=$(pgrep -f 'claude --dangerously' | head -1); "
    "[ -n \"$pid\" ] || exit 3; "
    "cmd=$(ps eww -p \"$pid\" -o command= 2>/dev/null); "
    "tok=$(printf '%s' \"$cmd\" | grep -oE 'CLAUDE_CODE_OAUTH_TOKEN=[^[:space:]]+' | head -1 | cut -d= -f2); "
    "[ -n \"$tok\" ] || exit 4; "
    "fp=$(printf '%s' \"$tok\" | sha256sum | cut -c1-12); "
    "mdl=$(printf '%s' \"$cmd\" | grep -oE -- '--model[ =][^[:space:]]+' | head -1 | sed -E 's/^--model[ =]//'); "
    "printf '%s %s\\n' \"$fp\" \"$mdl\""
)


def _remote_hub_scan(force: bool = False) -> Optional[dict]:
    """SSH the VPS hub, fingerprint its running token REMOTE-SIDE (raw token never
    leaves the VPS), read its --model. {"fp","model"} or None on any timeout/failure
    (=> UNVERIFIED). Cached _REMOTE_CACHE_TTL_S (positive AND negative, so a down
    host isn't hammered)."""
    now = time.monotonic()
    if not force and _remote_hub_cache["at"] and (now - _remote_hub_cache["at"]) < _REMOTE_CACHE_TTL_S:
        return _remote_hub_cache["val"]
    val = None
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=accept-new", "-i", _REMOTE_HUB_KEY,
             _REMOTE_HUB_HOST, _REMOTE_SCAN_SH],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            if parts and re.fullmatch(r"[0-9a-f]{12}", parts[0]):
                val = {"fp": parts[0], "model": (parts[1] if len(parts) > 1 and parts[1] else None)}
    except Exception:
        val = None
    _remote_hub_cache["at"] = now
    _remote_hub_cache["val"] = val
    return val


def token_ground_truth(include_remote: bool = False) -> dict:
    """GROUND-TRUTH per-body Claude account, read from the LIVE process env — the
    ACTUAL running token, never a declared value (op#10706/10715). For every claude
    proc on THIS host: tmux pane_pid -> claude pid -> `ps eww` reads
    CLAUDE_CODE_OAUTH_TOKEN, fingerprinted in-process (raw token NEVER returned,
    logged, or displayed) -> Max-account label; metered = non-empty ANTHROPIC_API_KEY.
    include_remote=True ALSO SSH-fingerprints the off-box VPS hub (op#10706 C) so
    the tool is source-of-truth regardless of host; unreachable -> UNVERIFIED.

    Each row: {session, account, fp, metered, model, host, verified, expected,
    expected_fp, mismatch}. `model` is the proc's actual --model (op#10717), None
    if unpinned. `expected`/`expected_fp` are the body's pointer-configured account
    (op#10706 R1); mismatch = live fp != expected fp.
    READ-ONLY: only `ps` + tmux list-panes, argv lists, no shell."""
    labels = _account_labels()
    pane_pids = _pid_to_session()
    ppid = _ppid_map()
    try:
        r = subprocess.run(
            ["ps", "eww", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        eww = r.stdout
    except Exception:
        eww = ""

    rows: List[dict] = []
    seen = set()
    for ln in eww.splitlines():
        low = ln.lower()
        # A fleet body = the claude CLI booted with --dangerously-skip-permissions
        # (workers, singletons, AND the console body all launch this way; bare
        # sub-process claude helpers do not, so this excludes them cleanly).
        if "claude" not in low or "--dangerously-skip-permissions" not in ln:
            continue
        m = re.match(r"\s*(\d+)\s", ln)
        if not m:
            continue
        pid = int(m.group(1))
        km = re.search(r"ANTHROPIC_API_KEY=(\S*)", ln)
        metered = bool(km and km.group(1))
        tok = re.search(r"CLAUDE_CODE_OAUTH_TOKEN=(\S+)", ln)
        fp = _fingerprint(tok.group(1)) if tok else None
        # MODEL ground truth (op#10717): the ACTUAL --model the live proc booted
        # with, read from its argv — never a declared value. Absent => the body is
        # on the CLI default (not pinned), reported as such, not guessed.
        mm = re.search(r"--model[= ](\S+)", ln)
        model = mm.group(1) if mm else None
        if metered:
            account = "metered (API)"
        elif fp is not None:
            account = labels.get(fp, "Max (unknown acct)")
        else:
            account = None  # token unreadable -> UNVERIFIED, never a guess
        verified = metered or fp is not None
        session = _session_for(pid, pane_pids, ppid) or ("pid:%d" % pid)
        seen.add(session)
        # EXPECTED = the body's OWN configured default (its pointer file / .env),
        # per orch-console #16038 — not a hardcoded account. A verified Max body
        # whose live fp != its expected fp is a MISMATCH (this is nazim-on-Syed).
        # Metered is its own alert; unverified is neither green nor a mismatch.
        exp_fp = _expected_fp(session)
        exp_account = (labels.get(exp_fp, "Max (unknown acct)")
                       if exp_fp else _EXPECTED_ACCOUNT)
        mismatch = bool(verified and not metered and exp_fp is not None and fp != exp_fp)
        rows.append({
            "session": session, "account": account, "fp": fp, "metered": metered,
            "model": model, "host": "Mini", "verified": verified,
            "expected": exp_account, "expected_fp": exp_fp, "mismatch": mismatch,
        })

    for sess, host in _REMOTE_BODIES.items():
        if sess in seen:
            continue
        exp_fp = _expected_fp(sess)
        exp_account = (labels.get(exp_fp, "Max (unknown acct)") if exp_fp else _EXPECTED_ACCOUNT)
        # Source-of-truth regardless of host (op#10706 C): SSH-fingerprint the hub.
        # Reachable -> VERIFIED (and can MISMATCH, e.g. hub-on-Syed vs pointer-Musa,
        # which we surface, never suppress). Unreachable / not requested -> UNVERIFIED.
        scan = _remote_hub_scan() if include_remote else None
        if scan and scan.get("fp"):
            rfp = scan["fp"]
            rows.append({
                "session": sess, "account": labels.get(rfp, "Max (unknown acct)"),
                "fp": rfp, "metered": False, "model": scan.get("model"), "host": host,
                "verified": True, "expected": exp_account, "expected_fp": exp_fp,
                "mismatch": bool(exp_fp is not None and rfp != exp_fp),
            })
        else:
            rows.append({
                "session": sess, "account": None, "fp": None, "metered": False,
                "model": None, "host": host, "verified": False,
                "expected": exp_account, "expected_fp": exp_fp, "mismatch": False,
            })

    # Loud first: mismatches, then metered, then unverified, then green by session.
    rows.sort(key=lambda x: (
        not x["mismatch"], not x["metered"], x["verified"], x["session"]))
    by_model: Dict[str, int] = {}
    for x in rows:
        if x.get("model"):
            by_model[x["model"]] = by_model.get(x["model"], 0) + 1
    _env_fp = _env_default_fp()
    summary = {
        "total": len(rows),
        "verified": sum(1 for x in rows if x["verified"]),
        "unverified": sum(1 for x in rows if not x["verified"]),
        "mismatched": sum(1 for x in rows if x["mismatch"]),
        "metered": sum(1 for x in rows if x["metered"]),
        # the fleet's .env default account, for reference (expected is now per-body)
        "expected_default": (labels.get(_env_fp, "Max (unknown acct)") if _env_fp else _EXPECTED_ACCOUNT),
        "by_model": by_model,
    }
    return {"rows": rows, "summary": summary}
