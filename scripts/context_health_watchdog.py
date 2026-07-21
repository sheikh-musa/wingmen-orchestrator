#!/usr/bin/env python3
"""context_health_watchdog.py — DORMANT context auto-reset watchdog (op#4676).

Closes the loop the gauge left open: the cc_session_costs gauge DETECTS context
bloat, but nothing acts on it. This watchdog reads the gauge and — when ARMED —
orchestrates the safe reset-from-handoff procedure (checkpoint -> /clear -> boot),
the same "reset-from-Mini" playbook a human runs by hand today.

  ┌─ SAFETY / STATUS ─────────────────────────────────────────────────────────┐
  │ EXECUTOR WIRED, but DRY-RUN by default and arm-gated.                     │
  │ - The active launchd plist runs --alert ONLY (detect + page). It is NOT   │
  │   --arm, so the destructive executor never runs from the scheduler.       │
  │ - Default mode is DRY-RUN: it DETECTS + LOGS what it *would* do and       │
  │   NEVER touches an agent. The reset path only executes under --arm AND    │
  │   after per-agent idle + AUTH + fresh-handoff verification all pass.      │
  │ - Every step re-verifies live; a mid-reset failure ABORTS + pages the     │
  │   operator LOUDLY and leaves the body in a SAFE state (never half-cleared │
  │   without a saved handoff). Self (orch-console) is NEVER auto-reset.      │
  └────────────────────────────────────────────────────────────────────────────┘

Thresholds (% of the model context window, default 1M):
  green  < SOFT (60%)
  amber  SOFT..HARD        -> CHECKPOINT-only (get a fresh handoff written), no reset
  red    >= HARD (80%)     -> full reset (armed + gates; else DRY-RUN/log)

Reset sequence (ARMED only — the from-Mini playbook a break-glass agent ran on the
hub on 2026-07-21, now codified; each step gated + re-verified live):
  1. pane must be IDLE (footer NOT "esc to interrupt") AND authenticated
  2. preserve any UNSENT input-box text (log verbatim, C-u clear, fold into nudge)
  3. a FRESH handoff must exist (mtime within HANDOFF_MAX_AGE_MIN); else checkpoint
     first and verify one appeared — never reset without a saved state
  4. /clear in place via tmux (type -> phantom-guard the text landed -> Enter)
  5. verify after clear: alive, authenticated, fresh prompt (auth broke -> page + stop)
  6. boot from the handoff (read boot_briefing/STATUS/handoff), verify it starts
Never /clear a non-idle, unauthenticated, or handoff-less agent; never kill-session.

Usage:
    context_health_watchdog.py                 # dry-run (default): detect + log only
    context_health_watchdog.py --json          # machine-readable classification
    context_health_watchdog.py --arm           # DANGER: enable the real reset path
                                               #   (still gated on idle + fresh handoff)
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ORCH_DIR = Path(__file__).resolve().parent.parent
# Self-contained package imports: under launchd there is NO PYTHONPATH, so
# `import nervous_system` (used by the alert formatter) ModuleNotFound'd and the
# whole alert-send path crashed SILENTLY — the hub hit 94% with zero page on
# 2026-07-21 for exactly this reason. Never depend on the env for our own imports.
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))
load_dotenv(_ORCH_DIR / ".env")

# Window + thresholds (overridable via env, mirrors the console gauge _CTX_WINDOW).
_CTX_WINDOW = int(os.environ.get("CONSOLE_CTX_WINDOW", "1000000"))
_SOFT = float(os.environ.get("CTX_WD_SOFT", "0.60"))   # amber -> checkpoint-nudge
_HARD = float(os.environ.get("CTX_WD_HARD", "0.80"))   # red   -> reset-eligible
_HANDOFF_MAX_AGE_MIN = int(os.environ.get("CTX_WD_HANDOFF_MAX_AGE_MIN", "30"))

# Always-on agents this watchdog governs. Each entry says WHERE the session lives
# so the (armed) reset can reach it, and where its handoff should be. host=None
# means "resolve at runtime"; kept explicit + reviewable rather than auto-magic.
# NOTE: this registry is intentionally hand-maintained — the reset touches live
# agents, so the set of resettable agents must be an explicit, audited decision.
#
# window  — this agent's REAL context window, so pct reflects ITS headroom (not a
#           blanket 1M). The Studio bodies run the fc-v12 1M window and DEGRADE as
#           they fill (no auto-compact) — those are the ones this watchdog exists
#           for. orch-console (this Mini body) runs the stock ~200K window WITH
#           auto-compact ON, so it self-heals long before any reset line: kept in
#           the registry for detection/symmetry but alerts=False (compaction, not a
#           watchdog reset, is its release valve). cai's live 539K reading proves
#           it, too, is on a >200K (1M) window.
# alerts  — whether an amber/red crossing pages the operator (nazim-console). Only
#           the degrade-prone 1M bodies alert; the self-compacting Mini body does
#           not (that would be noise for a condition it resolves on its own).
# handoff_dir — base dir (on the agent's host) the handoff_glob is relative to.
#   The hub's handoffs live in the Studio orchestrator checkout; cai's in its own
#   ~/wingmen/wingmen-cai checkout; Nazim's in this Mini checkout. Used to verify a
#   fresh handoff cross-host BEFORE any /clear.
# auto_reset — whether the ARMED executor may drive checkpoint/reset for this body.
#   ONLY the degrade-prone 1M Studio bodies. orch-console (self, Mini) is
#   self-compacting and IS this watchdog's own host — NEVER auto-reset self.
_AGENT_REGISTRY = {
    "cc-orchestrator": {"host": "mac-studio", "tmux": "orch",  "handoff_glob": "reports/session-handoff-*.md", "handoff_dir": "~/wingmen/orchestrator", "window": 1_000_000, "alerts": True,  "auto_reset": True,  "label": "The hub (orch, Studio)"},
    "cai":             {"host": "mac-studio", "tmux": "cai",   "handoff_glob": "reports/cai-handoff-*.md",     "handoff_dir": "~/wingmen/wingmen-cai",  "window": 1_000_000, "alerts": True,  "auto_reset": True,  "label": "cai (Studio)"},
    # window was hardcoded 200K (stale/wrong — op-caught 2026-07-21: the gauge showed
    # orch-console at 776K live tokens, impossible in a 200K window). Real window is
    # ~1M like the other bodies. Nazim DOES fill toward its limit and must be watched;
    # the difference from the Studio bodies is only the RELEASE VALVE — Claude Code
    # AUTO-COMPACTS (so alerts=True to warn, but auto_reset=False: recovery is a
    # compaction, not a /clear — and never reset the body the watchdog runs beside).
    "orch-console":    {"host": "self",        "tmux": "nazim", "handoff_glob": "reports/nazim-handoff-*.md",  "handoff_dir": str(_ORCH_DIR),           "window": 1_000_000, "alerts": True,  "auto_reset": False, "self_compacts": True, "label": "Nazim (console, Mini — auto-compacts)"},
}

# --- executor tunables (only consulted under --arm) ------------------------- #
# Waiting for a body to actually WRITE a handoff after a checkpoint nudge; the
# process blocks up to _CHECKPOINT_WAIT_S (the launchd cadence is 10min, so a
# multi-minute blocking wait is fine and far cheaper than a lost reset).
_CHECKPOINT_WAIT_S = int(os.environ.get("CTX_WD_CHECKPOINT_WAIT_S", "240"))
_CHECKPOINT_POLL_S = int(os.environ.get("CTX_WD_CHECKPOINT_POLL_S", "15"))
_POST_STEP_SETTLE_S = int(os.environ.get("CTX_WD_SETTLE_S", "8"))
# De-dup windows: don't re-checkpoint/re-reset the same body every cycle.
_CHECKPOINT_DEDUP_MIN = int(os.environ.get("CTX_WD_CHECKPOINT_DEDUP_MIN", "45"))
_RESET_DEDUP_MIN = int(os.environ.get("CTX_WD_RESET_DEDUP_MIN", "30"))
_EXEC_STATE_FILE = _ORCH_DIR / "logs" / "context_health_exec_state.json"


@dataclass
class AgentCtx:
    agent: str
    ctx_tokens: int
    pct: int
    level: str          # green | amber | red
    age_s: Optional[int]
    action: str         # ok | checkpoint-nudge | reset-eligible
    note: str = ""


def _dsn() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def read_context_gauge() -> list[AgentCtx]:
    """Freshest latest_context_tokens per always-on identity -> classification.

    Reuses the console's context-bloat query shape (DISTINCT ON, exclude
    operator-*, drop rows > window as bad data). Detection only; no side effects.
    """
    dsn = _dsn()
    if not dsn:
        return []
    try:
        import psycopg  # type: ignore
        connect = psycopg.connect
    except ImportError:  # pragma: no cover
        import psycopg2 as psycopg  # type: ignore
        connect = psycopg.connect

    out: list[AgentCtx] = []
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (cc_identity) cc_identity, latest_context_tokens,
              round(extract(epoch FROM (now() - COALESCE(ended_at, created_at))))::int AS age_s
            FROM cc_session_costs
            WHERE cc_identity NOT LIKE 'operator-%%'
              AND latest_context_tokens IS NOT NULL
              AND COALESCE(ended_at, created_at) > now() - interval '45 days'
            ORDER BY cc_identity, COALESCE(ended_at, created_at) DESC
            """
        )
        for ident, ctx, age_s in cur.fetchall():
            ctx = int(ctx or 0)
            # Per-agent window: a registered body's real window (1M for the Studio
            # bodies, 200K for the Mini), else the global default for transient lanes.
            window = _AGENT_REGISTRY.get(ident, {}).get("window", _CTX_WINDOW)
            if ctx <= 0 or ctx > window:
                continue  # bad data — a single call cannot exceed the window
            frac = ctx / window
            pct = round(frac * 100)
            if frac >= _HARD:
                level, action = "red", "reset-eligible"
            elif frac >= _SOFT:
                level, action = "amber", "checkpoint-nudge"
            else:
                level, action = "green", "ok"
            note = ""
            if action != "ok" and ident not in _AGENT_REGISTRY:
                note = "not in reset registry — detect-only"
            out.append(AgentCtx(ident, ctx, pct, level, age_s, action, note))
    out.sort(key=lambda a: a.pct, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# ARMED reset path — DISABLED by default. Every function here is a no-op unless
# the caller passed --arm; and even armed, the gates below must all pass.
# --------------------------------------------------------------------------- #

def _tmux_bin(host: str) -> str:
    """Resolve the tmux binary for a host.

    Local (host='self'): resolve via PATH — this Mini has tmux at
    /usr/local/bin/tmux, the Studio hub at /opt/homebrew/bin/tmux; hardcoding
    either breaks the other (this exact hardcode was a latent bug on the Mini).
    Remote: use an absolute path — a non-login ssh shell's PATH usually omits the
    homebrew dir (see the reset-from-Mini playbook); Studio is Apple Silicon, so
    default there is /opt/homebrew/bin/tmux, overridable via REMOTE_TMUX_BIN.
    """
    if host == "self":
        found = shutil.which("tmux")
        if found:
            return found
        for cand in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux"):
            if os.path.exists(cand):
                return cand
        return "tmux"
    return os.environ.get("REMOTE_TMUX_BIN", "/opt/homebrew/bin/tmux")


def _tmux_run(reg: dict, args: list[str], timeout: int = 20):
    """Run one tmux subcommand for `reg`, local or over ssh. Returns the
    CompletedProcess, or None on any transport failure. Uses argv (local) /
    shlex-quoted argv (remote) — never a raw shell string — so pane text and
    send-keys payloads cannot be shell-interpreted."""
    tbin = _tmux_bin(reg["host"])
    try:
        if reg["host"] == "self":
            return subprocess.run([tbin, *args], capture_output=True, text=True, timeout=timeout)
        remote = " ".join(shlex.quote(x) for x in [tbin, *args])
        return subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", f"Musa@{reg['host']}", remote],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None


def _capture_pane(reg: dict, lines: int = 40) -> Optional[str]:
    """Last `lines` of the agent's tmux pane (cross-host). None if unreachable."""
    r = _tmux_run(reg, ["capture-pane", "-t", reg["tmux"], "-p"])
    if r is None or r.returncode != 0:
        return None
    return "\n".join(r.stdout.splitlines()[-lines:])


_BUSY_MARKER = "esc to interrupt"
# Markers that mean the pane is NOT a normal authenticated Claude prompt (a
# login / model-picker / error / expired-session screen). If ANY appears we
# refuse to act — that is a break-glass condition, never an auto-reset target.
_UNAUTH_MARKERS = (
    "select login method", "sign in to claude", "log in with", "please run /login",
    "invalid api key", "authentication_error", "authentication error",
    "select a model", "choose a model", "credit balance is too low",
    "api error: 401", "session expired", "not logged in", "welcome to claude code",
)


@dataclass
class PaneState:
    reachable: bool
    idle: Optional[bool]          # True=idle, False=busy, None=unknown
    authenticated: Optional[bool] # True=normal prompt, False=bad screen, None=unsure
    input_text: str = ""          # best-effort unsent text in the prompt box
    raw: str = ""


def _extract_input_text(pane: str) -> str:
    """Best-effort: pull UNSENT text out of the Claude Code prompt box.

    The box renders a line like `│ > typed words                 │`. We take the
    content after the first `>` on that line, drop the box rule, and ignore the
    dimmed placeholder (`Try ...`). Conservative: any doubt -> "". This only ever
    ADDS a preserve-note; it is never used to justify DISCARDING input."""
    for line in reversed(pane.splitlines()):
        if "│ >" not in line and "│>" not in line:
            continue
        after = line.split(">", 1)[1].replace("│", " ").strip()
        if not after or after.startswith("Try ") or after.startswith("? for"):
            return ""
        return after
    return ""


def _pane_state(reg: dict) -> PaneState:
    """Classify the agent pane: reachable / idle / authenticated / unsent-input."""
    pane = _capture_pane(reg)
    if pane is None:
        return PaneState(reachable=False, idle=None, authenticated=None)
    low = pane.lower()
    busy = _BUSY_MARKER in low
    unauth = any(m in low for m in _UNAUTH_MARKERS)
    # Authenticated = a normal prompt: no known bad-screen marker AND showing the
    # busy footer, the prompt box, or the shortcut hint (i.e. a live CC session).
    has_prompt = busy or ("│ >" in pane) or ("? for shortcuts" in low)
    if unauth:
        authed: Optional[bool] = False
    elif has_prompt:
        authed = True
    else:
        authed = None  # unrecognised screen -> caller treats as NOT-safe
    return PaneState(reachable=True, idle=not busy, authenticated=authed,
                     input_text=_extract_input_text(pane), raw=pane)


def _agent_is_idle(reg: dict) -> Optional[bool]:
    """True if the agent pane is idle. None if unknown/unreachable. (Thin wrapper
    over _pane_state, kept for the planner + tests.)"""
    return _pane_state(reg).idle


def _newest_handoff(reg: dict) -> Optional[tuple[str, float]]:
    """(name, mtime_epoch) of the newest handoff matching the glob, cross-host.

    Local: python glob. Remote (macOS Studio — `find -printf` is unavailable):
    `ls -t <glob> | head -1` then `stat -f %m`. Handoff names carry no spaces.
    None if none found / unreachable."""
    glob = reg["handoff_glob"]
    if reg["host"] == "self":
        base = Path(os.path.expanduser(reg.get("handoff_dir", str(_ORCH_DIR))))
        newest, newest_m = None, 0.0
        for p in base.glob(glob):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > newest_m:
                newest, newest_m = p.name, m
        return (newest, newest_m) if newest else None
    base = reg.get("handoff_dir", "~/wingmen/orchestrator")
    remote = (f"cd {base} 2>/dev/null && f=$(ls -t {glob} 2>/dev/null | head -1); "
              f'[ -n "$f" ] && echo "$(stat -f %m $f) $f"')
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", f"Musa@{reg['host']}", remote],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        mtime_s, name = r.stdout.strip().split(" ", 1)
        return (name, float(mtime_s))
    except ValueError:
        return None


def _fresh_handoff(reg: dict) -> Optional[str]:
    """Name of the newest handoff if it is within HANDOFF_MAX_AGE_MIN, else None."""
    nh = _newest_handoff(reg)
    if nh and (time.time() - nh[1]) <= _HANDOFF_MAX_AGE_MIN * 60:
        return nh[0]
    return None


def plan_reset(a: AgentCtx, armed: bool) -> str:
    """Return the DECISION string (a pure planner — no side effects beyond the
    read-only pane/handoff probes it makes when `armed`). The actual
    checkpoint/clear/boot execution lives in run_executor(); this only describes
    what would/should happen so the classification line + --json stay honest.
    Staged: amber -> checkpoint-only, red+idle+fresh-handoff -> full reset."""
    reg = _AGENT_REGISTRY.get(a.agent)
    if a.action == "ok":
        return "ok"
    if a.action == "checkpoint-nudge":
        if reg and not reg.get("auto_reset"):
            return f"amber {a.pct}% — detect-only body (self-compacting), no checkpoint"
        return f"{'WOULD ' if not armed else ''}checkpoint-nudge {a.agent} (amber {a.pct}%) — writes a fresh handoff, no reset"
    # reset-eligible (red)
    if not reg:
        return f"detect-only: {a.agent} red {a.pct}% but not in reset registry"
    if not reg.get("auto_reset"):
        return f"detect-only: {a.agent} red {a.pct}% — self-compacting body, never auto-reset"
    if not armed:
        return (f"DRY-RUN: would full-reset {a.agent} (red {a.pct}%) IF idle+authed+fresh-handoff "
                f"(checkpoint if stale -> /clear -> boot)")
    idle = _agent_is_idle(reg)
    handoff = _fresh_handoff(reg)
    gate = []
    if idle is not True:
        gate.append(f"NOT-IDLE({idle})")
    if handoff is None:
        gate.append("NO-FRESH-HANDOFF(will-checkpoint-first)")
    if gate:
        return f"ARMED: {a.agent} red {a.pct}% — {','.join(gate)}; executor handles this cycle"
    return f"ARMED: RESET-READY {a.agent} (red {a.pct}%, idle+handoff {handoff}) — executor runs this cycle"


# --------------------------------------------------------------------------- #
# ARMED EXECUTOR — the real checkpoint / /clear / boot driver (op#5516 follow-up,
# codifying the from-Mini reset a break-glass agent ran on the hub on 2026-07-21).
# Runs ONLY when --arm is passed AND every per-step gate holds. Wrapped in a
# dead-man's-switch: any failure mid-reset pages the operator LOUDLY and leaves
# the body in a SAFE state (never half-cleared without a saved handoff).
# --------------------------------------------------------------------------- #

# Chars that break `tmux send-keys` quoting / could be shell-interpreted. Our own
# nudge wording avoids them; folded operator text is scrubbed of them (the
# verbatim original is written to the preserve-log first, so nothing is lost).
_FORBIDDEN_NUDGE_CHARS = "'\"()`$;|&\n\r\t"


def _sanitize_nudge(s: str) -> str:
    out = "".join(" " if c in _FORBIDDEN_NUDGE_CHARS else c for c in s)
    return " ".join(out.split())


def _send_literal(reg: dict, text: str) -> bool:
    """tmux send-keys -l <text> (literal — no key-name lookup)."""
    r = _tmux_run(reg, ["send-keys", "-t", reg["tmux"], "-l", text])
    return bool(r and r.returncode == 0)


def _send_key(reg: dict, key: str) -> bool:
    """tmux send-keys <key> (named key: Enter, C-u, ...)."""
    r = _tmux_run(reg, ["send-keys", "-t", reg["tmux"], key])
    return bool(r and r.returncode == 0)


def _load_exec_state() -> dict:
    try:
        return json.loads(_EXEC_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_exec_state(state: dict) -> None:
    try:
        _EXEC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _EXEC_STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def _page_loud(text: str) -> None:
    """Dependency-free operator page (does NOT import nervous_system) — the
    dead-man's-switch path. CTX_WD_ALERT_STDOUT=1 prints instead (for tests)."""
    if os.environ.get("CTX_WD_ALERT_STDOUT") == "1":
        print("[PAGE-would-send]\n" + text + "\n")
        return
    try:
        subprocess.run([str(_ORCH_DIR / "scripts" / "nazim_send.sh"), text],
                       timeout=30, cwd=str(_ORCH_DIR))
    except Exception as e:  # pragma: no cover — a page must never crash the loop
        print(f"[ctx-health] loud page failed: {e}", file=sys.stderr)


def _checkpoint_nudge(a: AgentCtx, reg: dict, folded: str = "") -> str:
    hint = reg["handoff_glob"].replace("*", "NOW")
    base = (f"CONTEXT CHECKPOINT auto-watchdog: you are at about {a.pct} percent of your context window. "
            f"Write a FRESH handoff file {hint} capturing current state, open threads and next actions, "
            f"then persist a session_digest. This is a clean restore point before any reset.")
    if folded:
        base += (f" NOTE you had unsent text in your input box, preserved here as an open thread: {folded}")
    return _sanitize_nudge(base)


def _boot_nudge(reg: dict) -> str:
    hint = reg["handoff_glob"].replace("*", "latest")
    return _sanitize_nudge(
        f"BOOT after auto-reset: read boot_briefing then STATUS.md then your latest handoff {hint}. "
        f"Reconcile operator_log.unprocessed and your agent_messages inbox, then resume. "
        f"Confirm you are oriented before acting.")


def _preserve_input_box(reg: dict, st: PaneState) -> str:
    """If the prompt box holds unsent operator text: log it VERBATIM first, then
    clear the box (C-u), and return a sanitized copy to fold into the nudge.
    Never silently clobbers — the verbatim text is durably logged before any key."""
    txt = (st.input_text or "").strip()
    if not txt:
        return ""
    try:
        _EXEC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        with (_ORCH_DIR / "logs" / "context_health_preserved_input.log").open("a") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {reg['tmux']}: {txt}\n")
    except OSError:
        pass
    _send_key(reg, "C-u")  # clear the box so the nudge types into a clean line
    return _sanitize_nudge(txt)[:400]


def _do_checkpoint(a: AgentCtx, reg: dict, st: PaneState) -> tuple[bool, str]:
    """Nudge the body to write a fresh handoff, wait, verify one appeared that is
    NEWER than any pre-existing handoff. Caller must have already confirmed the
    idle+authenticated gates. Returns (ok, detail)."""
    before = _newest_handoff(reg)
    before_m = before[1] if before else 0.0
    t0 = time.time()
    folded = _preserve_input_box(reg, st)
    nudge = _checkpoint_nudge(a, reg, folded)
    if not _send_literal(reg, nudge) or not _send_key(reg, "Enter"):
        return False, "send-keys checkpoint nudge failed"
    deadline = t0 + _CHECKPOINT_WAIT_S
    while time.time() < deadline:
        time.sleep(_CHECKPOINT_POLL_S)
        nh = _newest_handoff(reg)
        if nh and nh[1] > before_m and (time.time() - nh[1]) <= _HANDOFF_MAX_AGE_MIN * 60:
            return True, f"fresh handoff {nh[0]}"
    return False, f"no fresh handoff after {_CHECKPOINT_WAIT_S}s"


def _do_reset(a: AgentCtx, reg: dict) -> tuple[bool, str]:
    """Full from-Mini reset: gate -> (checkpoint if stale) -> re-verify -> /clear
    (phantom-guarded, in-place, NEVER kill-session) -> verify -> boot. Any step
    failure ABORTS and pages loudly, leaving the body in a safe state."""
    st = _pane_state(reg)
    if not st.reachable:
        return False, "SKIP: pane unreachable"
    if st.idle is not True:
        return False, f"SKIP: not idle (idle={st.idle}) — never reset a busy body"
    if st.authenticated is not True:
        return False, f"SKIP: not clearly authenticated (auth={st.authenticated})"

    # 1. Guarantee a fresh saved state. Checkpoint if the handoff is stale, OR if
    #    there is unsent operator text we must fold in before /clear discards it.
    fresh = _fresh_handoff(reg)
    if (not fresh) or st.input_text.strip():
        ok, detail = _do_checkpoint(a, reg, st)
        if not ok:
            _page_loud(f"🚨 ctx-watchdog ABORTED reset of {a.agent}: checkpoint failed ({detail}). "
                       f"Body left INTACT — never /clear without a saved handoff. Manual reset needed.")
            return False, f"ABORT: checkpoint failed ({detail})"
        fresh = detail

    # Re-verify idle+auth immediately before the destructive step (state may have
    # changed while the body was writing its handoff).
    st2 = _pane_state(reg)
    if st2.idle is not True or st2.authenticated is not True:
        return False, f"SKIP: state changed before /clear (idle={st2.idle} auth={st2.authenticated})"

    # 2. /clear in place, phantom-guarded: type it, VERIFY it landed in the box,
    #    only THEN press Enter. Never kill-session.
    if not _send_literal(reg, "/clear"):
        _page_loud(f"🚨 ctx-watchdog: failed to type /clear for {a.agent}. Body INTACT (handoff {fresh}).")
        return False, "ABORT: /clear type failed"
    time.sleep(2)
    guard = _capture_pane(reg) or ""
    if "/clear" not in guard:
        _send_key(reg, "C-u")  # scrub whatever half-typed rather than blind-Enter
        _page_loud(f"🚨 ctx-watchdog: phantom-guard FAILED for {a.agent} (/clear not in the box) — did "
                   f"NOT press Enter. Body INTACT (handoff {fresh}).")
        return False, "ABORT: phantom-guard failed"
    if not _send_key(reg, "Enter"):
        _page_loud(f"🚨 ctx-watchdog: failed to submit /clear for {a.agent}. Body INTACT (handoff {fresh}).")
        return False, "ABORT: /clear submit failed"
    time.sleep(_POST_STEP_SETTLE_S)

    # 3. Verify after clear: alive, still authenticated, fresh prompt.
    st3 = _pane_state(reg)
    if not st3.reachable:
        _page_loud(f"🚨 ctx-watchdog: {a.agent} pane UNREACHABLE after /clear — may need break-glass. "
                   f"Handoff {fresh} was saved first.")
        return False, "ABORT: unreachable after clear"
    if st3.authenticated is False:
        _page_loud(f"🚨 ctx-watchdog: {a.agent} auth BROKE after /clear — needs break-glass login. "
                   f"Handoff {fresh} was saved first.")
        return False, "ABORT: auth broke after clear"

    # 4. Boot from the handoff.
    if not _send_literal(reg, _boot_nudge(reg)) or not _send_key(reg, "Enter"):
        _page_loud(f"🚨 ctx-watchdog: {a.agent} cleared OK but the BOOT nudge failed to send — it is at an "
                   f"empty prompt, please boot it. Handoff {fresh}.")
        return False, "ABORT: boot nudge send failed"
    time.sleep(_POST_STEP_SETTLE_S)
    st4 = _pane_state(reg)
    booting = (st4.idle is False) or ("boot" in (st4.raw or "").lower())
    if not booting:
        _page_loud(f"⚠️ ctx-watchdog: {a.agent} cleared + boot nudge sent but no activity yet — verify it "
                   f"started reading. Handoff {fresh}.")
    return True, f"reset OK (handoff {fresh}; booting={booting})"


def run_executor(rows: list[AgentCtx]) -> list[str]:
    """ARMED entry point. Per body: amber -> checkpoint-only, red -> full reset.
    De-duped via _EXEC_STATE_FILE; each body wrapped in a dead-man's-switch so a
    crash pages loudly instead of dying silent. Only touches auto_reset bodies
    (never self / orch-console)."""
    state = _load_exec_state()
    now = time.time()
    results: list[str] = []
    for a in rows:
        reg = _AGENT_REGISTRY.get(a.agent)
        if not reg or not reg.get("auto_reset") or a.action == "ok":
            continue  # detect-only / self body / green — never auto-act
        est = state.get(a.agent, {})
        try:
            if a.level == "amber":
                last_cp = float(est.get("checkpoint_at", 0) or 0)
                if (now - last_cp) < _CHECKPOINT_DEDUP_MIN * 60 and _fresh_handoff(reg):
                    results.append(f"{a.agent}: amber — checkpoint deduped (fresh handoff exists)")
                    continue
                st = _pane_state(reg)
                if not st.reachable:
                    results.append(f"{a.agent}: amber — pane unreachable, skip")
                    continue
                if st.idle is not True or st.authenticated is not True:
                    results.append(f"{a.agent}: amber — not idle/authed (idle={st.idle} auth={st.authenticated}), skip")
                    continue
                ok, detail = _do_checkpoint(a, reg, st)
                if ok:
                    est["checkpoint_at"] = now
                    state[a.agent] = est
                else:
                    _page_loud(f"⚠️ ctx-watchdog: amber checkpoint of {a.agent} did not confirm a handoff ({detail}).")
                results.append(f"{a.agent}: amber checkpoint {'OK' if ok else 'FAILED'} — {detail}")

            elif a.level == "red":
                last_rs = float(est.get("reset_at", 0) or 0)
                if (now - last_rs) < _RESET_DEDUP_MIN * 60:
                    results.append(f"{a.agent}: red — reset deduped (last {int((now - last_rs) / 60)}m ago)")
                    continue
                ok, detail = _do_reset(a, reg)
                if ok:
                    est["reset_at"] = now
                    est["checkpoint_at"] = now
                    state[a.agent] = est
                results.append(f"{a.agent}: red reset {'OK' if ok else 'SKIP/ABORT'} — {detail}")
        except Exception as e:  # dead-man's-switch: never die silent mid-reset
            import traceback
            traceback.print_exc()
            _page_loud(f"🐛 ctx-watchdog EXECUTOR CRASHED mid-action on {a.agent}: {e}. The body may be "
                       f"mid-reset — CHECK IT NOW. The watchdog is not guarding the fleet until fixed.")
            results.append(f"{a.agent}: EXECUTOR EXCEPTION {e}")
    _save_exec_state(state)
    return results


# --------------------------------------------------------------------------- #
# Degrade ALERT path — separate from --arm. This pages the operator when a 1M
# body fogs so it gets reset before he notices; it never touches an agent, so it
# is safe to run every cycle (unlike the reset executor). Opt-in via --alert so
# manual inspection runs stay silent. De-duped via a small state file: alert on a
# level RISE (green->amber->red), re-nag a sustained red hourly, reset on green.
# --------------------------------------------------------------------------- #

_STATE_FILE = _ORCH_DIR / "logs" / "context_health_state.json"
_RENAG_MIN = int(os.environ.get("CTX_WD_RENAG_MIN", "60"))
_LEVEL_RANK = {"green": 0, "amber": 1, "red": 2}


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass  # best-effort — a lost state file just means one extra (idempotent) alert


def _send_alert(text: str) -> None:
    """Deliver a degrade alert on nazim-console (Nazim's own operator voice).

    CTX_WD_ALERT_STDOUT=1 prints instead of sending — for testing the decision
    logic without paging the operator.
    """
    if os.environ.get("CTX_WD_ALERT_STDOUT") == "1":
        print("[ALERT-would-send]\n" + text + "\n")
        return
    try:
        subprocess.run(
            [str(_ORCH_DIR / "scripts" / "nazim_send.sh"), text],
            timeout=30, cwd=str(_ORCH_DIR),
        )
    except Exception as e:  # pragma: no cover — alert delivery must not crash the watchdog
        print(f"[ctx-health] alert send failed: {e}", file=sys.stderr)


def _alert_text(a: AgentCtx, reg: dict) -> str:
    label = reg.get("label", a.agent)
    if reg.get("self_compacts"):
        # This body (Claude Code) AUTO-COMPACTS as its release valve — the harness
        # summarizes its own context before it fogs, so there is nothing to /clear.
        # The alert is INFORMATIONAL, not a reset alarm (op-caught 2026-07-21: the
        # generic 'reset before it fogs' copy kept telling the operator to reset a
        # body that heals itself).
        return (f"ℹ️ {label} at ~{a.pct}% — no action needed, it auto-compacts.\n"
                f"{label} is at ~{a.pct}% of its window ({a.ctx_tokens:,} tokens); Claude Code "
                f"summarizes its own context before it fogs, so there's nothing to reset. "
                f"Checkpoint a fresh handoff if it's mid-thread so continuity is clean through the compaction.")
    try:
        from nervous_system.alert_format import format_alert
    except Exception:
        # A DELIVERED plain alert beats a pretty one that crashes into silence.
        head = "🚨 near-full — reset before it fogs" if a.level == "red" else "⚠️ context filling"
        tail = ("Reset it (checkpoint handoff -> in-place /clear -> boot) — say the word and I'll drive it."
                if a.level == "red" else
                "Checkpoint if you're mid-thread; I'll page again if it crosses the reset line.")
        return (f"{head}: {label} (~{a.pct}%)\n"
                f"{label} is at ~{a.pct}% of its 1M window ({a.ctx_tokens:,} tokens, age {a.age_s}s) "
                f"and does not auto-compact. {tail}")
    if a.level == "amber":
        return format_alert(
            icon="⚠️", title=f"{label} context filling (~{a.pct}%)",
            what=f"{label} is at ~{a.pct}% of its 1M window and does not auto-compact.",
            why="Left unchecked it degrades (gets foggy/slow) instead of resetting cleanly.",
            do="No action yet — I'll page again if it crosses the reset line. Checkpoint if you're mid-thread.",
            detail=f"{a.agent}: {a.ctx_tokens:,} tokens, age {a.age_s}s. amber≥{_SOFT:.0%}.",
        )
    return format_alert(
        icon="🚨", title=f"{label} near-full (~{a.pct}%) — reset before it fogs",
        what=f"{label} is at ~{a.pct}% of its 1M window — the point where a non-compacting body starts degrading.",
        why="A fogged body gives slow/incoherent answers and can post stale rulings; catching it here beats you noticing it.",
        do="Reset it via the from-Mini playbook (checkpoint handoff -> in-place /clear -> boot). Say the word and I'll drive it.",
        detail=f"{a.agent}: {a.ctx_tokens:,} tokens, age {a.age_s}s. red≥{_HARD:.0%}.",
    )


def run_alerts(rows: list[AgentCtx]) -> list[str]:
    """Page the operator for alert-enabled bodies that rose to amber/red. Returns
    the list of agent names alerted this cycle (for logging)."""
    import time
    state = _load_state()
    now = time.time()
    fired: list[str] = []
    for a in rows:
        reg = _AGENT_REGISTRY.get(a.agent)
        if not reg or not reg.get("alerts"):
            continue
        cur_rank = _LEVEL_RANK.get(a.level, 0)
        prev = state.get(a.agent, {})
        prev_rank = _LEVEL_RANK.get(prev.get("level", "green"), 0)
        prev_ts = float(prev.get("alerted_at", 0) or 0)
        if cur_rank == 0:  # green — clear so the next rise re-alerts
            state.pop(a.agent, None)
            continue
        rose = cur_rank > prev_rank
        renag = cur_rank == _LEVEL_RANK["red"] and (now - prev_ts) >= _RENAG_MIN * 60
        if rose or renag:
            _send_alert(_alert_text(a, reg))
            state[a.agent] = {"level": a.level, "alerted_at": now}
            fired.append(a.agent)
        else:
            # Hold: keep the level current but preserve alerted_at so the red re-nag
            # timer keeps counting from the last real page (not from every cycle).
            state[a.agent] = {"level": a.level, "alerted_at": prev_ts}
    _save_state(state)
    return fired


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--arm", action="store_true",
                    help="DANGER: enable the reset decision path (still gated on idle+handoff)")
    ap.add_argument("--alert", action="store_true",
                    help="page the operator (nazim-console) on amber/red for alert-enabled bodies")
    args = ap.parse_args()

    rows = read_context_gauge()
    if args.json:
        out = [{**asdict(r), "plan": plan_reset(r, args.arm)} for r in rows]
        exec_results = run_executor(rows) if args.arm else []
        print(json.dumps({"rows": out, "exec": exec_results}, indent=2))
        if args.alert:
            run_alerts(rows)
        return 0

    reset_mode = "ARMED — LIVE EXECUTOR" if args.arm else "DRY-RUN (detect+plan only)"
    alert_mode = "ON" if args.alert else "off"
    print(f"[ctx-health] soft={_SOFT:.0%} hard={_HARD:.0%} reset={reset_mode} alert={alert_mode}")
    if not rows:
        print("[ctx-health] no context telemetry (writer running?)")
        return 0
    for r in rows:
        win = _AGENT_REGISTRY.get(r.agent, {}).get("window", _CTX_WINDOW)
        print(f"  {r.level:5} {r.agent:16} {r.pct:3}% of {win//1000}K  ({r.ctx_tokens:>9,})  age={r.age_s}s  -> {plan_reset(r, args.arm)}")
    if args.arm:
        print("[ctx-health] ARMED: running executor (checkpoint/reset, gated + deduped)...")
        for line in run_executor(rows):
            print(f"    exec: {line}")
    if args.alert:
        fired = run_alerts(rows)
        if fired:
            print(f"[ctx-health] paged operator for: {', '.join(fired)}")
    return 0


if __name__ == "__main__":
    # Dead-man's-switch: a watchdog that dies silently is worse than none (the hub
    # hit 94% unwarned on 2026-07-21 because an unhandled crash in the alert path
    # exited 1 with no page). If ANYTHING here throws, page the operator via the
    # dependency-free subprocess path (does NOT import nervous_system) so the
    # failure of the guard is itself surfaced.
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
                 f"🐛 Context watchdog CRASHED — it is NOT guarding the fleet right now: {_e}. "
                 f"Fix before relying on context alerts."],
                timeout=30, cwd=str(_ORCH_DIR),
            )
        except Exception:
            pass
        sys.exit(1)
