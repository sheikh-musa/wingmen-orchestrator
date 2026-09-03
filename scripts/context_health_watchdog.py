#!/usr/bin/env python3
"""context_health_watchdog.py — DORMANT context auto-reset watchdog (op#4676).

Closes the loop the gauge left open: the cc_session_costs gauge DETECTS context
bloat, but nothing acts on it. This watchdog reads the gauge and — when ARMED —
orchestrates the safe reset-from-handoff procedure (checkpoint -> /clear -> boot),
the same "reset-from-Mini" playbook a human runs by hand today.

  ┌─ SAFETY / STATUS ─────────────────────────────────────────────────────────┐
  │ EXECUTOR WIRED, DRY-RUN by default, GRANULARLY arm-gated (CAI-RESP-500).  │
  │ Arm levels: off | amber | red.                                            │
  │ - off  (default / no --arm): DETECTS + LOGS what it *would* do, touches   │
  │   NO agent.                                                                │
  │ - amber (`--arm=amber`, the ACTIVE plist mode): the WRITE-ONLY half only  │
  │   — nudges ≥SOFT bodies to write a fresh handoff. ZERO-RISK: it NEVER     │
  │   runs /clear. A RED body under amber is checkpoint-only + dry-run note.  │
  │ - red  (`--arm=red` / bare `--arm`): ALSO the destructive /clear+boot     │
  │   reset, and ONLY after per-agent idle + AUTH + fresh-handoff all pass.   │
  │   GATED by cai on stricter conditions — kept UNARMED until those are met. │
  │ - The active launchd plist runs `--alert --arm=amber`: detect + page +    │
  │   auto-checkpoint; the destructive /clear executor never runs from the    │
  │   scheduler.                                                               │
  │ - Every reset step re-verifies live; a mid-reset failure ABORTS + pages   │
  │   the operator LOUDLY and leaves the body SAFE (never half-cleared        │
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
  7. PROVE it cleared from cc_session_costs telemetry (a NEW session_id whose
     latest_context_tokens collapsed). Success is reported ONLY on that proof —
     send-keys returning 0 is not evidence a session restarted. Three outcomes,
     kept distinct all the way out to the operator's phone: confirmed-reset /
     confirmed-NOT-reset / unconfirmed (the writer runs every ~300s, so lag is
     real and must never be reported as either).
Never /clear a non-idle, unauthenticated, or handoff-less agent; never kill-session.

Usage:
    context_health_watchdog.py                 # dry-run (default): detect + log only
    context_health_watchdog.py --json          # machine-readable classification
    context_health_watchdog.py --arm=amber     # WRITE-ONLY: auto-checkpoint ≥SOFT bodies,
                                               #   NEVER /clear (zero-risk; the plist mode)
    context_health_watchdog.py --arm=red       # DANGER: also the /clear+boot reset path
    context_health_watchdog.py --arm           #   (bare --arm == --arm=red, legacy: both)
                                               #   red still gated on idle + auth + fresh handoff
    # Env equivalent: CTX_WD_ARM=off|amber|red (the --arm flag overrides it).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
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

# CAI-RESP-501: the watchdog (pen iii) is now held via the fleet_health_lease
# single-owner lease (default holder = cc-fleet-health; hub reclaims on expiry).
# Self-contained import (no PYTHONPATH under launchd — see the note above).
from scripts.lib import fleet_health_lease, fleet_health_boundaries  # noqa: E402
from scripts.lib import fire_window  # noqa: E402  (quiesce during a recycle fire window)


def _logs_dir() -> Path:
    """Where this module's SIDE-EFFECT logs go.

    WHY THIS EXISTS (2026-07-26 incident, second harm channel): the test suite
    wrote its fixtures straight into the real audit logs. `logs/pen_gate.log`
    accumulated `red reset DEFERRED ... 3 unhandled operator message(s)` trios and
    `logs/context_health_preserved_input.log` accumulated `orch: deploy the fix
    now` lines — none of which ever happened — interleaved with GENUINE captures
    (e.g. the operator's real unsent `now do giro` at 2026-07-26T02:13:34Z) with
    nothing to tell them apart. An audit log that contains fiction is not an audit
    log; the next person root-causing a reset reads invented evidence.

    Same shape as the paging opt-out in `_in_pytest`: the isolation must NOT depend
    on every future test remembering to patch a path. CTX_WD_TEST_LOG_DIR lets the
    conftest point it at a per-run tmp dir; otherwise a throwaway temp dir."""
    if _in_pytest():
        d = Path(os.environ.get("CTX_WD_TEST_LOG_DIR")
                 or (Path(tempfile.gettempdir()) / "ctx_wd_test_logs"))
    else:
        d = _ORCH_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_pen_gate(msg: str) -> None:
    """Append a pen-gate decision to the shared gate log (best-effort, mirrors
    tg_send.sh's pen_gate.log). Test-scoped under pytest — see _logs_dir()."""
    try:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(_logs_dir() / "pen_gate.log", "a") as fh:
            fh.write(f"{stamp} ctx-watchdog {msg}\n")
    except Exception:
        pass

# Window + thresholds (overridable via env, mirrors the console gauge _CTX_WINDOW).
_CTX_WINDOW = int(os.environ.get("CONSOLE_CTX_WINDOW", "1000000"))
_SOFT = float(os.environ.get("CTX_WD_SOFT", "0.60"))   # amber -> checkpoint-nudge
_HARD = float(os.environ.get("CTX_WD_HARD", "0.80"))   # red   -> reset-eligible
_HANDOFF_MAX_AGE_MIN = int(os.environ.get("CTX_WD_HANDOFF_MAX_AGE_MIN", "30"))
# A reading older than this is NOT current state — it is the last thing the
# writer happened to catch. The gauge computed age_s from the first day and no
# consumer has ever looked at it, so a 4-day-old row printed as a live green
# reading indistinguishable from one taken 30 seconds ago (cc-cosem, age
# 368,939s, shown "green 16%" on 2026-07-26 while the body had not written a
# transcript line since 21 July). Stale readings are now MARKED, never
# suppressed — a body that has since blown past its number must not read as
# healthy, and a silenced alert would be the worse failure.
_STALE_S = int(os.environ.get("CTX_WD_STALE_MIN", "20")) * 60

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
    # external_recycle (CAI-RESP-1360): the hub is a long-lived, harness-compacted, EXTERNALLY-
    # recycled singleton (recovered ONLY via an external recycle — the exact operator command is
    # parameterized in _HUB_RECYCLE_REMEDIATION, pinned by orch-console; a multi-week session is
    # NORMAL). It does NOT self-recycle — cai REJECTED giving it self_compacts (that would relocate
    # the false-alarm to a 70%+ self-recycle nudge the hub cannot act on). Amber/steady-state is
    # EXPECTED, not degradation, so it does NOT operator-page; it pages ONLY at the ceiling
    # (>=95%) or on a frozen/non-advancing gauge, and the remediation names the external recycle.
    # PAGING-ONLY flag: auto_reset stays True (the executor path is untouched — this classifies
    # escalation copy in run_alerts, exactly as self_compacts does, and touches no reset branch).
    "cc-orchestrator": {"host": "mac-studio", "tmux": "orch",  "handoff_glob": "reports/session-handoff-*.md", "handoff_dir": "~/wingmen/orchestrator", "window": 1_000_000, "alerts": True,  "auto_reset": True,  "external_recycle": True, "inbox_scope": "hub", "label": "The hub (orch, Studio)"},
    # self_compacts added in S2 (PURELY ADDITIVE — Nazim point 2): cai is a Claude Code
    # body that auto-compacts, so it can self-recycle on a fresh handoff. The flag ONLY
    # routes the nudge/alert-copy path (run_alerts); it touches NO auto_reset branch —
    # run_executor selects bodies by auto_reset and never reads self_compacts, so cai's
    # reset path is unchanged. I NUDGE cai, I NEVER reset it (its Tier-C self-arm only).
    "cai":             {"host": "mac-studio", "tmux": "cai",   "handoff_glob": "reports/cai-handoff-*.md",     "handoff_dir": "~/wingmen/wingmen-cai",  "window": 1_000_000, "alerts": True,  "auto_reset": True,  "self_compacts": True, "inbox_scope": "cai", "label": "cai (Studio)"},
    # window was hardcoded 200K (stale/wrong — op-caught 2026-07-21: the gauge showed
    # orch-console at 776K live tokens, impossible in a 200K window). Real window is
    # ~1M like the other bodies. Nazim DOES fill toward its limit and must be watched;
    # the difference from the Studio bodies is only the RELEASE VALVE — Claude Code
    # AUTO-COMPACTS (so alerts=True to warn, but auto_reset=False: recovery is a
    # compaction, not a /clear — and never reset the body the watchdog runs beside).
    "orch-console":    {"host": "self",        "tmux": "nazim", "handoff_glob": "reports/nazim-handoff-*.md",  "handoff_dir": str(_ORCH_DIR),           "window": 1_000_000, "alerts": True,  "auto_reset": False, "self_compacts": True, "label": "Nazim (console, Mini — auto-compacts)"},
    # #40 gap-close (op-caught: cc-quality hit 95% with ZERO page). These two fell
    # BETWEEN both daemons' body-selection: auto_recycle_on_bloat enumerates WORKER
    # panes (excludes them as singleton-ish); this watchdog's registry never listed
    # them. Now WATCHED here: alerts:True so bloat pages, auto_reset:False so they are
    # NEVER executor-/clear'd — cc-quality is on-demand (CAI-729, no hb loop) and the
    # SRE (self) is NEVER auto-reset (lease renewal is its dead-man's switch, CAI-501).
    # Recovery for both is a SELF-recycle (self_recycle.sh), never a driven /clear.
    # self_compacts added in S2: cc-quality auto-compacts and can self-recycle, so it now
    # gets the self-recycle NUDGE (no plain operator page) instead of the S1 page-on-rise.
    # auto_reset stays False (on-demand body, CAI-729 — NEVER driven /clear).
    "cc-quality":      {"host": "self",        "tmux": "quality",      "handoff_glob": "reports/quality-handoff-*.md",      "handoff_dir": str(_ORCH_DIR), "window": 1_000_000, "alerts": True, "auto_reset": False, "self_compacts": True, "label": "cc-quality (Mini, on-demand — no hb loop)"},
    # cc-fleet-health (the SRE, self) — registered in S2 with SELF-NUDGE behaviour. It was
    # deliberately deferred before because a plain alert would PAGE THE OPERATOR about the
    # SRE ("reset it"). S2's decouple removes that: self_compacts:True routes it through the
    # self-recycle NUDGE (tell the body, it decides the seam — trigger inversion) with NO
    # operator page; alerts:False so it never takes the plain page; auto_reset:False so it is
    # NEVER executor-/clear'd (assert_no_sre_red_reset + lease-renewal dead-man's switch,
    # CAI-501). The operator is only paged as the BACKSTOP if the SRE ignores its own nudge
    # past the persisted threshold (self-recycle demonstrably failed = a stuck SRE).
    "cc-fleet-health": {"host": "self",        "tmux": "fleet-health", "handoff_glob": "reports/fleet-health-handoff-*.md", "handoff_dir": str(_ORCH_DIR), "window": 1_000_000, "alerts": False, "auto_reset": False, "self_compacts": True, "label": "cc-fleet-health (the SRE, self — self-nudge only)"},
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

# --- reset CONFIRMATION (op#6xxx, 2026-07-26) ------------------------------- #
# How long to wait for cc_session_costs to PROVE the session actually restarted,
# and how hard the token count must collapse to count as proof. Empirical (a
# genuine hub reset at 2026-07-26T02:55Z): session adc34035-… @ 802,287 tokens
# became f58a2fb4-… @ 91,270 — an 11% residue. Half the pre-reset count is a very
# loose ceiling by that measure, chosen so a slightly-late reading (the body has
# already begun re-reading its handoff) still confirms, while "nothing happened"
# (the count barely moves, or only grows) never can.
_RESET_CONFIRM_WAIT_S = int(os.environ.get("CTX_WD_RESET_CONFIRM_WAIT_S", "90"))
_RESET_CONFIRM_POLL_S = int(os.environ.get("CTX_WD_RESET_CONFIRM_POLL_S", "10"))
_RESET_COLLAPSE_FRAC = float(os.environ.get("CTX_WD_RESET_COLLAPSE_FRAC", "0.5"))
# The cc_session_costs writer's launchd cadence (dev.wingmen.cc-session-costs-writer,
# StartInterval 300). Confirmation can therefore LEGITIMATELY lag past our window —
# which is why "could not confirm" is its own outcome and never collapses into
# either "confirmed" or "confirmed-not".
_COST_WRITER_INTERVAL_S = int(os.environ.get("CTX_WD_COST_WRITER_INTERVAL_S", "300"))

# The three — and only three — things we are allowed to say about a /clear we
# submitted. Keeping them as named constants (not ad-hoc strings at each call
# site) is deliberate: the 2026-07-26 false page happened because "we typed it"
# and "it happened" were the same value.
CONFIRM_RESET = "confirmed-reset"        # PROOF: new session_id + collapsed tokens
CONFIRM_NOT_RESET = "confirmed-not-reset"  # PROOF of failure: post-/clear telemetry, same session
CONFIRM_UNKNOWN = "unconfirmed"          # no proof either way inside the window


@dataclass
class AgentCtx:
    agent: str
    ctx_tokens: int
    pct: int
    level: str          # green | amber | red
    age_s: Optional[int]
    action: str         # ok | checkpoint-nudge | reset-eligible
    note: str = ""
    # True when the freshest telemetry row for this body is older than
    # _STALE_S. The number is then a HISTORICAL reading, not current state.
    # Appended last, with a default, so existing positional/keyword callers
    # and tests construct unchanged.
    stale: bool = False

    @property
    def age_label(self) -> str:
        """Human age, with STALE called out. Never presents an old reading as now."""
        if self.age_s is None:
            return "age=unknown"
        if self.stale:
            return f"age={self.age_s}s STALE(>{_STALE_S // 60}m)"
        return f"age={self.age_s}s"


def _dsn() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


# A transient DB-connect/DNS blip to the substrate pooler must NOT be able to kill
# the whole safety watchdog (2026-08-31: an unhandled psycopg.OperationalError —
# "failed to resolve host ...pooler.supabase.com" at the read_context_gauge connect
# — exited the process and false-paged "watchdog CRASHED" for something that
# self-heals on the very next StartInterval tick). We retry within the run first;
# if the gauge is STILL unreachable, we raise GaugeUnreachable so main() can tell a
# transient single-run skip (soft: log + clean exit, self-heals next tick) apart
# from a PERSISTENT outage (loud page — a watchdog blind for N runs IS a real
# dead-man's-switch condition). A raw crash is neither honest signal.
_GAUGE_CONNECT_ATTEMPTS = 3          # attempts within a single run
_GAUGE_CONNECT_BACKOFF_S = 2.0       # linear backoff: 2s, 4s between attempts
_GAUGE_UNREACHABLE_LOUD_AFTER = 2    # consecutive unreachable RUNS before paging loud (~20m at StartInterval 600)
_GAUGE_TICK_MIN = 10                 # StartInterval 600s, for human-readable messages


class GaugeUnreachable(Exception):
    """The context gauge (substrate) was unreachable after bounded retries this run.
    A distinct type so main() never confuses a transient DB blip with a real bug."""


def read_context_gauge(dropped: Optional[list] = None) -> list[AgentCtx]:
    """Freshest latest_context_tokens per always-on identity -> classification.

    Reuses the console's context-bloat query shape (DISTINCT ON, exclude
    operator-*). Detection only; no side effects.

    `dropped`: optional list the caller passes in to COLLECT readings this
    function refuses to classify, as (identity, ctx_tokens, window, reason)
    tuples. Over-window rows used to be `continue`d silently — a measurement
    that hides its own failure, so a body whose window we have wrong simply
    VANISHES from the gauge and reads as "not monitored" rather than "not
    measurable". Callers that pass nothing behave exactly as before.
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

    # Bounded retry on the CONNECT — the exact failure point (transient DNS/pooler
    # blip). A momentary resolve failure now recovers within the run instead of
    # crashing the whole watchdog; a still-dead connection after retries raises
    # GaugeUnreachable (handled softly-then-loudly by main), never a raw traceback.
    conn = None
    for _attempt in range(_GAUGE_CONNECT_ATTEMPTS):
        try:
            conn = connect(dsn)
            break
        except psycopg.OperationalError as _e:
            if _attempt == _GAUGE_CONNECT_ATTEMPTS - 1:
                raise GaugeUnreachable(str(_e)) from _e
            time.sleep(_GAUGE_CONNECT_BACKOFF_S * (_attempt + 1))
    out: list[AgentCtx] = []
    with conn, conn.cursor() as cur:
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
                # NOT silently skipped any more. ctx > window does not mean the
                # body is fine — it means our window for it is wrong (or the
                # writer is), and the honest report is "cannot measure", not
                # absence. Surfaced to the caller; never classified, so a bogus
                # number can still never trigger a reset.
                if dropped is not None:
                    reason = "non-positive reading" if ctx <= 0 else "exceeds assumed window"
                    dropped.append((ident, ctx, window, reason))
                continue
            frac = ctx / window
            pct = round(frac * 100)
            if frac >= _HARD:
                level, action = "red", "reset-eligible"
            elif frac >= _SOFT:
                level, action = "amber", "checkpoint-nudge"
            else:
                level, action = "green", "ok"
            stale = age_s is not None and age_s > _STALE_S
            notes = []
            if action != "ok" and ident not in _AGENT_REGISTRY:
                notes.append("not in reset registry — detect-only")
            if stale:
                notes.append(
                    f"STALE: last telemetry {age_s // 60}m old — this is the body's LAST KNOWN "
                    f"reading, not its current one; if it has been working since, it is higher")
            out.append(AgentCtx(ident, ctx, pct, level, age_s, action, "; ".join(notes), stale))
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
    bg_agents: int = 0            # in-flight background agents (a /clear discards them)
    raw: str = ""

    def __post_init__(self) -> None:
        """Type-assert every field so a MIS-BOUND argument fails loudly, here.

        WHY THIS EXISTS (2026-07-26 incident, contributing cause): `bg_agents` was
        inserted into the field order AHEAD of `raw`. Every pre-existing positional
        construction — `PaneState(True, True, True, "", "reading boot_briefing")` —
        then silently bound the RAW PANE TEXT to `bg_agents` and left `raw` empty.
        Nothing errored. A string is truthy, so `if st.bg_agents:` in
        _verify_capture_before_clear started firing the background-agent probe on
        every such call (that is where the fabricated `background-agent guard
        cleared` lines in logs/pen_gate.log came from), while `raw` — which step 4
        reads to decide whether the body is booting — was silently "".
        Python 3.9 has no dataclasses.KW_ONLY, so we cannot make mis-binding
        impossible; we make it IMMEDIATE AND LOUD instead. Field order in a
        safety-critical struct must never again be a silent-reinterpretation
        hazard: adding a field can still be done, it just cannot be done quietly.
        """
        def _bad(name: str, want: str, got) -> TypeError:
            return TypeError(
                f"PaneState.{name} must be {want}, got {type(got).__name__}={got!r}. "
                f"This is almost certainly a POSITIONAL construction binding to the "
                f"wrong field after a field-order change — construct PaneState with "
                f"KEYWORD arguments.")
        if not isinstance(self.reachable, bool):
            raise _bad("reachable", "bool", self.reachable)
        if self.idle is not None and not isinstance(self.idle, bool):
            raise _bad("idle", "bool or None", self.idle)
        if self.authenticated is not None and not isinstance(self.authenticated, bool):
            raise _bad("authenticated", "bool or None", self.authenticated)
        if not isinstance(self.input_text, str):
            raise _bad("input_text", "str", self.input_text)
        # bool is a subclass of int — exclude it explicitly, or PaneState(..., True)
        # would sail through as "1 background agent".
        if isinstance(self.bg_agents, bool) or not isinstance(self.bg_agents, int):
            raise _bad("bg_agents", "int (the COUNT of background agents)", self.bg_agents)
        if not isinstance(self.raw, str):
            raise _bad("raw", "str (the captured pane text)", self.raw)


def _extract_input_text(pane: str) -> str:
    """Best-effort: pull UNSENT text out of the Claude Code prompt box.

    The box renders a line like `│ > typed words                 │`. We take the
    content after the first `>` on that line, drop the box rule, and ignore the
    dimmed placeholder (`Try ...`). Conservative: any doubt -> "". This only ever
    ADDS a preserve-note; it is never used to justify DISCARDING input."""
    for line in reversed(pane.splitlines()):
        # MODERN TUI (2026-07-25): the composer renders as '❯' + U+00A0 NON-BREAKING
        # SPACE + text — no box rule at all. The legacy '│ >' patterns below match
        # NOTHING against it, so this extractor silently returned "" for every live
        # pane, and _verify_capture_before_clear's condition (c) — "input box holds no
        # unsent text" — passed by never being able to see any. A guard that cannot
        # observe what it guards fails OPEN while reading as protection. Verified
        # against real panes: cai held "reset me", cc-cosem-exams held "check the
        # inbox", and both extracted as "" before this fix.
        if line.startswith("\u276f"):
            after = line[1:].replace("\u00a0", " ").strip()
            if not after or after.startswith("Try ") or after.startswith("? for"):
                return ""
            if after.startswith("Press up to edit"):   # queued-message hint, not a draft
                return ""
            return after
        if "│ >" not in line and "│>" not in line:
            continue
        after = line.split(">", 1)[1].replace("│", " ").strip()
        if not after or after.startswith("Try ") or after.startswith("? for"):
            return ""
        return after
    return ""


# A body "Waiting for N background agents to finish" shows NO busy marker — the footer
# looks idle because the main loop IS idle. But a /clear discards those agents and their
# work. Observed 2026-07-25: cai sat at 92% context, idle by every existing check, with 4
# background agents 26+ minutes into the research feeding its next deliverable. Every
# CAI-500 precondition passed. Only a human reading the pane footer would have caught it.
_BG_AGENT_RE = re.compile(r"waiting for\s+(\d+)\s+background agent", re.I)


_BG_TIMER_RE = re.compile(r"(\d+m\s*\d+s)")


def _background_agents(pane: str) -> int:
    """Count in-flight background agents in a pane. 0 when none / unknown."""
    m = _BG_AGENT_RE.search(pane or "")
    return int(m.group(1)) if m else 0


def _bg_agents_live(reg: dict, settle_s: int = 20) -> tuple[int, str]:
    """Are the background agents ACTUALLY RUNNING, or a stale footer?

    Two samples, because the footer LIES. Observed 2026-07-26: cai's pane showed
    "Waiting for 4 background agents to finish ... 26m 42s" — the identical string, with
    the identical elapsed time, across a /clear AND a full reset, for hours. They were
    stale duplicates from a relaunch after the token-cap deaths; cai itself ruled them
    expendable. A live agent's timer ADVANCES; a phantom's does not.

    This matters as much as the guard it protects: keying a refusal off a single sample
    would have blocked EVERY future cai reset forever on a footer nobody can clear. A
    guard that never lets anything through is not safer than one that lets everything
    through — it just fails in the direction that looks responsible.

    Returns (count, reason). count==0 means "nothing live in the way".
    """
    first = _capture_pane(reg)
    n = _background_agents(first or "")
    if not n:
        return 0, "no background agents"
    t1 = _BG_TIMER_RE.search(first or "")
    time.sleep(settle_s)
    second = _capture_pane(reg)
    if _background_agents(second or "") == 0:
        return 0, "background agents finished during the settle window"
    t2 = _BG_TIMER_RE.search(second or "")
    if t1 and t2 and t1.group(1) == t2.group(1):
        return 0, (f"{n} background agent(s) shown but the elapsed timer is FROZEN at "
                   f"{t1.group(1)} across {settle_s}s — stale footer, not live work")
    return n, f"{n} background agent(s) live (timer advancing)"


def _pane_state(reg: dict) -> PaneState:
    """Classify the agent pane: reachable / idle / authenticated / unsent-input."""
    pane = _capture_pane(reg)
    if pane is None:
        return PaneState(reachable=False, idle=None, authenticated=None)
    low = pane.lower()
    busy = _BUSY_MARKER in low
    unauth = any(m in low for m in _UNAUTH_MARKERS)
    # Authenticated = a normal prompt: no known bad-screen marker AND showing a
    # live-CC-session signal. We match BOTH the old TUI (│ > box, "? for shortcuts")
    # AND the current TUI (a "❯" prompt between horizontal rules, with a
    # "⏵⏵ bypass permissions … ← for agents" footer) — the old markers alone missed
    # the current TUI, so cai/orch read as authed=None and the watchdog skipped even
    # the safe checkpoint (op#6179). Footer markers are footer-specific and only
    # reached when NO unauth marker is present, so a login/error screen still wins.
    has_prompt = (
        busy
        or ("│ >" in pane) or ("│>" in pane)          # old TUI prompt box
        or ("? for shortcuts" in low)                 # old TUI shortcut hint
        or ("for agents" in low)                      # current TUI idle footer ("← for agents")
        or ("bypass permissions" in low)              # current TUI permission-mode footer
    )
    if unauth:
        authed: Optional[bool] = False
    elif has_prompt:
        authed = True
    else:
        authed = None  # unrecognised screen -> caller treats as NOT-safe
    return PaneState(reachable=True, idle=not busy, authenticated=authed,
                     input_text=_extract_input_text(pane),
                     bg_agents=_background_agents(pane), raw=pane)


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


def plan_reset(a: AgentCtx, armed: bool = False, arm_level: Optional[str] = None) -> str:
    """Return the DECISION string (a pure planner — no side effects beyond the
    read-only pane/handoff probes it makes when the RED executor is armed). The
    actual checkpoint/clear/boot execution lives in run_executor(); this only
    describes what would/should happen so the classification line + --json stay
    honest.

    Granular arming (CAI-RESP-500): `arm_level` is off | amber | red.
      - off   : pure DRY-RUN, no execution, no live probes.
      - amber : the WRITE-ONLY half only — amber bodies get a checkpoint nudge,
                and a RED body is STILL only checkpointed (never /clear'd); its
                plan shows a dry-run reset so the operator sees red was NOT reset.
      - red   : both — amber checkpoints AND red bodies may be /clear-reset
                (still gated live on idle+auth+fresh-handoff).
    `armed` (legacy bool) maps to arm_level red when True, off when False, and is
    only consulted when arm_level is not given (keeps older callers/tests valid).
    The invariant: red /clear can ONLY happen when arm_level == 'red'."""
    if arm_level is None:
        arm_level = "red" if armed else "off"
    reg = _AGENT_REGISTRY.get(a.agent)
    if a.action == "ok":
        return "ok"
    if a.action == "checkpoint-nudge":
        if reg and not reg.get("auto_reset"):
            return f"amber {a.pct}% — detect-only body (self-compacting), no checkpoint"
        will_checkpoint = arm_level in ("amber", "red")
        return f"{'' if will_checkpoint else 'WOULD '}checkpoint-nudge {a.agent} (amber {a.pct}%) — writes a fresh handoff, no reset"
    # reset-eligible (red)
    if not reg:
        return f"detect-only: {a.agent} red {a.pct}% but not in reset registry"
    if not reg.get("auto_reset"):
        return f"detect-only: {a.agent} red {a.pct}% — self-compacting body, never auto-reset"
    if a.stale:
        # A destructive /clear must never be justified by a reading we cannot
        # vouch for as current. Checkpointing a stale body is still fine (and
        # still happens under amber) — that only writes a handoff.
        return (f"detect-only: {a.agent} red {a.pct}% but the reading is STALE ({a.age_s}s) — "
                f"refusing to justify a /clear on non-current telemetry; checkpoint only")
    if arm_level != "red":
        # off OR amber — the destructive /clear half is UNARMED. Under amber we
        # still checkpoint the red body (write-only, ≥SOFT), but NEVER reset it.
        if arm_level == "amber":
            return (f"DRY-RUN (arm=amber): WOULD reset {a.agent} (red {a.pct}%) — red executor UNARMED; "
                    f"checkpoint-only this cycle (write-only handoff, no /clear)")
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


def _in_fire_window(reg: dict) -> bool:
    """True when a recycle currently owns this body's pane.

    A keystroke landing between a reset's composer wipe and its /clear Enter jams the
    clear and the body comes back half-initialised. This watchdog's nudges are signal
    only — the checkpoint ask is durable — so standing off for the few seconds of a
    fire window costs nothing and the next poll re-evaluates."""
    sess = str(reg.get("tmux") or "").lstrip("=").split(":")[0]
    return bool(sess) and fire_window.is_held(sess)


def _send_literal(reg: dict, text: str) -> bool:
    """tmux send-keys -l <text> (literal — no key-name lookup)."""
    if _in_fire_window(reg):
        return False
    r = _tmux_run(reg, ["send-keys", "-t", reg["tmux"], "-l", text])
    return bool(r and r.returncode == 0)


def _send_key(reg: dict, key: str) -> bool:
    """tmux send-keys <key> (named key: Enter, C-u, ...)."""
    if _in_fire_window(reg):
        return False
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


def _in_pytest() -> bool:
    """True when running under pytest.

    WHY THIS EXISTS (2026-07-26 incident, root-caused): the operator received two
    real Telegram pages — "cc-orchestrator cleared + boot nudge sent" — asserting a
    hub reset that never happened. They were not sent by the launchd watchdog
    (which runs --arm=amber and has never taken the destructive path). They were
    sent by `tests/test_context_health_watchdog.py::test_do_reset_full_happy_path`,
    from an ordinary `pytest` run, because that one test does not monkeypatch
    _page_loud. A false ALL-CLEAR is worse than a false alarm: it makes the reader
    NOT act. A governance body then repeated it and filed it against itself.

    Opting out of the operator's phone must NOT depend on every future test
    remembering to patch the right seam — that is the same "safety property that
    depends on everyone remembering" we removed from nudge_cai.sh. It lives here.
    CTX_WD_ALERT_STDOUT=1 remains the explicit override for non-pytest callers.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _page_loud(text: str) -> None:
    """Dependency-free operator page (does NOT import nervous_system) — the
    dead-man's-switch path. CTX_WD_ALERT_STDOUT=1 prints instead (for tests)."""
    if os.environ.get("CTX_WD_ALERT_STDOUT") == "1" or _in_pytest():
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
        from datetime import datetime, timezone
        # Test-scoped under pytest (see _logs_dir): the operator's REAL unsent text
        # is the only thing that may ever land in this file.
        with (_logs_dir() / "context_health_preserved_input.log").open("a") as fh:
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


# --------------------------------------------------------------------------- #
# CAI-500 condition 1 — "idle = NO OWED ACTION in flight". A body is red-reset-
# eligible ONLY with NOTHING owed: operator inbox drained + no open in-flight
# executor + pane idle. These two probes are the DB boundary (mockable seams for
# the unit tests). Both fail-SAFE toward "owed" (return None) so we NEVER /clear a
# body we cannot PROVE is quiescent.
# --------------------------------------------------------------------------- #

_INBOX_SCOPE_SQL = {
    # Mirrors nervous_system/operator_log._channel_scope_sql: the HUB owns every
    # operator surface EXCEPT the other bodies' DMs + shared feeds; cai owns only
    # its own channel. A body with an UNDECLARED scope -> None -> treated as owed.
    "hub": (" AND channel <> 'tmux-console'"
            " AND tag IS DISTINCT FROM 'nazim-console'"
            " AND tag IS DISTINCT FROM 'cai-channel'"),
    "cai": (" AND tag = 'cai-channel'"),
}


def _pg_connect():
    """psycopg(2) connect callable, or None if neither is importable."""
    try:
        import psycopg  # type: ignore
        return psycopg.connect
    except ImportError:  # pragma: no cover
        try:
            import psycopg2  # type: ignore
            return psycopg2.connect
        except ImportError:
            return None


def _bus_nudge_self_recycle(agent: str, pct: int, connect=None, dsn: Optional[str] = None) -> bool:
    """Tell the BLOATED BODY it is bloated. Returns True only if the row was written.

    TRIGGER INVERSION (operator op#13520: "here i am telling you youre bloated and
    having to push a button"). This detector has always reported to a HUMAN, and the
    executor that would have acted on it needed an arm-sign — because one body clearing
    ANOTHER is acting on an inference about someone else's state, and three of four such
    inferences were wrong on 2026-08-15. Since 6eb9d01 the body can recycle ITSELF, which
    removes the inference entirely: it is the only party that knows whether it is
    mid-thought, and it bears the whole cost of being wrong.

    So this is a NOTIFICATION, not an action: an ordinary attributable bus row that the
    body drains like any other work and is free to decline. It writes nothing to the
    body's pane, so it is not the watchdog pen ACTING and needs no lease — and it
    deliberately does not tell the body to recycle NOW, only that it is at the line.
    """
    connect = connect or _pg_connect()
    dsn = dsn or _dsn()
    if not connect or not dsn:
        return False
    body = (
        f"CONTEXT AT ~{pct}% — you are the one who decides what happens next.\n\n"
        f"You auto-compact rather than fog, so nothing breaks if you ride. But compaction is "
        f"LOSSY: it keeps what a summarizer picked, not what you would have picked. The operator "
        f"ruled on that directly (op#13418) — it is the fallback, not the fix.\n\n"
        f"The better path, and it needs no button from anyone:\n"
        f"  1. Write a FRESH handoff (self_recycle refuses one older than 900s — recycling onto a "
        f"stale restore point does not preserve the work, it launders the loss).\n"
        f"  2. scripts/self_recycle.sh --reset <your reset script> --handoff <your handoff> --dry-run\n"
        f"  3. Then fire it for real and stop producing output; the clear lands after your turn ends.\n\n"
        f"THIS IS YOUR CALL, not an order. If you are mid-ruling or mid-build, finish and recycle "
        f"straight after — say so on the bus so nobody wonders. Nobody else can tell your 'finished' "
        f"from your 'waiting', which is exactly why the decision is yours."
    )
    try:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
                "priority, requires_response, is_test) VALUES (%s, %s, 'update', %s, %s, 'P2', false, false)",
                ("cc-fleet-health", agent,
                 f"You are at ~{pct}% — deliberate self-recycle beats letting it auto-compact (your call)",
                 body),
            )
            conn.commit()
        return True
    except Exception as e:  # pragma: no cover — a failed nudge must never crash the watchdog
        print(f"[ctx-health] self-recycle bus nudge failed for {agent}: {e}", file=sys.stderr)
        return False


def _unhandled_operator_count(reg: dict) -> Optional[int]:
    """Count of UNHANDLED inbound operator_messages in this body's channel scope.
    0 = inbox drained (no owed operator action). None = INDETERMINATE (DB down, no
    driver, or the body has no declared inbox scope) -> the caller MUST treat as
    owed and DEFER (never /clear a body whose inbox we cannot prove drained)."""
    scope = reg.get("inbox_scope")
    if scope not in _INBOX_SCOPE_SQL:
        return None
    dsn = _dsn()
    connect = _pg_connect()
    if not dsn or connect is None:
        return None
    try:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM operator_messages "
                "WHERE direction='inbound' AND handled_at IS NULL"
                + _INBOX_SCOPE_SQL[scope])
            return int(cur.fetchone()[0])
    except Exception:
        return None


def _open_executor_count(a: AgentCtx, reg: dict) -> Optional[int]:
    """Count of OPEN in-flight exec_work_items owned by this body (state claimed/
    running). 0 = none in flight. None = INDETERMINATE (DB down, or the exec-
    reliability layer's table is not yet deployed) -> treated as owed -> DEFER.
    This deliberately keeps red UNREACHABLE until the exec layer exists to PROVE
    no work-item is mid-flight — the safe direction for a DISARMED path."""
    dsn = _dsn()
    connect = _pg_connect()
    if not dsn or connect is None:
        return None
    try:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM exec_work_items "
                "WHERE claimed_by=%s AND state IN ('claimed','running')",
                (a.agent,))
            return int(cur.fetchone()[0])
    except Exception:
        return None


def _owed_action_in_flight(a: AgentCtx, reg: dict, st: PaneState) -> Optional[str]:
    """CAI-500 condition 1. Return a REASON string iff ANY owed action is in
    flight (so the red /clear must DEFER this cycle), else None (quiescent -> may
    proceed to capture+clear). Owed = pane not idle, OR operator inbox not
    PROVABLY drained, OR an open in-flight executor. INDETERMINATE probes count AS
    owed — we never /clear a body we cannot PROVE has nothing owed."""
    if st.idle is not True:
        return f"pane not idle (idle={st.idle}) — mid-task"
    n_inbox = _unhandled_operator_count(reg)
    if n_inbox is None:
        return "operator-inbox drain UNPROVABLE (DB down / undeclared scope) — defer"
    if n_inbox > 0:
        return f"{n_inbox} unhandled operator message(s) owed in '{reg.get('inbox_scope')}' scope"
    n_exec = _open_executor_count(a, reg)
    if n_exec is None:
        return "open-executor state UNPROVABLE (DB down / exec table absent) — defer"
    if n_exec > 0:
        return f"{n_exec} open in-flight executor(s) for {a.agent}"
    return None


# --------------------------------------------------------------------------- #
# RESET CONFIRMATION — the difference between "we typed /clear" and "it cleared".
#
# WHY THIS EXISTS (2026-07-26 incident, the harm itself): the operator was paged
# twice with "cc-orchestrator cleared + boot nudge sent". Nothing had been reset.
# The claim rested on four observations that are all TRUE of a body that was never
# touched: send-keys returned 0, the captured pane contained the substring
# "/clear", Enter returned 0, and afterwards the pane was reachable and not showing
# a login screen. A reachable, authenticated Claude pane looks IDENTICAL before and
# after a /clear — so that evidence cannot distinguish "cleared" from "nothing
# happened" from "typed into the wrong pane". A false ALL-CLEAR is worse than a
# false alarm: it makes the reader stop looking.
#
# The proof already existed in the substrate and was simply never consulted:
# cc_session_costs carries session_id + latest_context_tokens per identity, and a
# real reset shows a NEW session_id with the token count collapsed. Verified on the
# genuine 02:55Z hub reset: adc34035-… @ 802,287 -> f58a2fb4-… @ 91,270.
# --------------------------------------------------------------------------- #

def _session_fingerprint(agent: str) -> Optional[tuple]:
    """(session_id, latest_context_tokens, observed_epoch, db_now_epoch) for the
    FRESHEST cc_session_costs row of `agent`. None = INDETERMINATE (no DSN/driver/
    row, or the query failed) — never an assertion that the body has no session.

    Times come from the DB, not this host: the reference clock for "did the writer
    run AFTER the /clear" must be one clock, or clock skew between the Mini and the
    substrate turns a lag into a refutation (or worse, the reverse).
    """
    dsn = _dsn()
    connect = _pg_connect()
    if not dsn or connect is None:
        return None
    try:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT session_id, latest_context_tokens, "
                "       extract(epoch FROM COALESCE(ended_at, created_at)), "
                "       extract(epoch FROM now()) "
                "FROM cc_session_costs "
                "WHERE cc_identity=%s AND session_id IS NOT NULL "
                "  AND latest_context_tokens IS NOT NULL "
                "ORDER BY COALESCE(ended_at, created_at) DESC LIMIT 1",
                (agent,))
            row = cur.fetchone()
            if not row:
                return None
            return (str(row[0]), int(row[1] or 0), float(row[2]), float(row[3]))
    except Exception:
        return None


def _confirm_reset(agent: str, before: Optional[tuple],
                   wait_s: Optional[int] = None,
                   poll_s: Optional[int] = None) -> tuple[str, str]:
    """Did the /clear ACTUALLY restart the session? Returns (state, detail) where
    state is exactly one of:

      CONFIRM_RESET      — PROOF it reset: a NEW session_id whose latest_context_tokens
                           collapsed to <= _RESET_COLLAPSE_FRAC of the pre-reset count.
                           ONLY this licenses the word "cleared" to a human.
      CONFIRM_NOT_RESET  — PROOF it did NOT: the telemetry writer produced a row AFTER
                           our /clear and it is STILL the same session. The body was
                           not reset; treat the attempt as failed and look at it.
      CONFIRM_UNKNOWN    — no proof either way inside the window. The writer runs on a
                           ~300s interval, so silence here is genuinely ambiguous: it
                           may have reset and not been measured yet, or not reset at
                           all. It must NOT be reported as either.

    Keeping UNKNOWN distinct from NOT_RESET is the whole point. Folding it into
    success recreates the incident; folding it into failure would train the operator
    to ignore a page that fires on ordinary writer lag. It gets its own wording.

    `before` is the pre-/clear fingerprint from _session_fingerprint(); None means we
    never had a baseline, and a collapse cannot be measured against nothing -> UNKNOWN.
    """
    wait_s = _RESET_CONFIRM_WAIT_S if wait_s is None else wait_s
    poll_s = _RESET_CONFIRM_POLL_S if poll_s is None else poll_s
    if not before:
        return CONFIRM_UNKNOWN, (
            f"no pre-/clear telemetry baseline for {agent} — a context collapse cannot be "
            f"measured against nothing")
    b_sid, b_tok, _b_obs, _b_now = before
    ceiling = int(b_tok * _RESET_COLLAPSE_FRAC)
    # DB-clock reading of "just after the /clear", taken on the first readable poll.
    # A row observed later than this is post-/clear telemetry and can refute.
    t_clear_db: Optional[float] = None
    last = "cc_session_costs never became readable"
    deadline = time.time() + wait_s
    while True:
        fp = _session_fingerprint(agent)
        if fp is None:
            last = "cc_session_costs unreadable (DB down / no driver / no row)"
        else:
            sid, tok, obs, db_now = fp
            if t_clear_db is None:
                t_clear_db = db_now
            if sid != b_sid and tok <= ceiling:
                pctres = round(100 * tok / max(b_tok, 1))
                return CONFIRM_RESET, (
                    f"new session {sid[:8]} at {tok:,} tokens (was {b_sid[:8]} at {b_tok:,}) — "
                    f"context collapsed to {pctres}% of the pre-reset reading")
            if sid != b_sid:
                # A new session that did NOT collapse is not a /clear signature. Do
                # not claim success off a session_id alone.
                last = (f"session changed to {sid[:8]} but context did NOT collapse "
                        f"({tok:,} vs {b_tok:,} — ceiling {ceiling:,})")
            elif obs > t_clear_db:
                return CONFIRM_NOT_RESET, (
                    f"the telemetry writer produced a row {int(obs - t_clear_db)}s AFTER the "
                    f"/clear and it is STILL session {sid[:8]} at {tok:,} tokens — the session "
                    f"did not restart")
            else:
                last = (f"still session {sid[:8]} at {tok:,} tokens, last written "
                        f"{int(db_now - obs)}s ago — no post-/clear telemetry yet")
        if time.time() >= deadline:
            return CONFIRM_UNKNOWN, (
                f"{last}; no proof within {wait_s}s. The cc_session_costs writer runs every "
                f"~{_COST_WRITER_INTERVAL_S}s, so this may be measurement lag rather than a "
                f"failed reset — it is NOT evidence of either")
        time.sleep(poll_s)


def _verify_capture_before_clear(reg: dict, st: PaneState) -> tuple[bool, str]:
    """CAI-500 condition 2. PROVE that all un-drained input was durably captured
    BEFORE the irreversible /clear. Returns (ok, detail); if capture cannot be
    VERIFIED the caller ABORTS (never clears). Verifies: (a) a FRESH handoff is on
    disk (state saved + verified within the age window), (b) the operator inbox is
    STILL drained — re-read here, so nothing slipped in during the checkpoint
    window, (c) the prompt input box holds NO unsent text (any was preserved +
    C-u cleared during the checkpoint step)."""
    fresh = _fresh_handoff(reg)
    if not fresh:
        return False, "no fresh handoff on disk — state NOT saved"
    n_inbox = _unhandled_operator_count(reg)
    if n_inbox is None:
        return False, "cannot re-verify operator inbox is drained (DB/scope) — refuse"
    if n_inbox > 0:
        return False, f"{n_inbox} operator message(s) arrived during checkpoint — inbox NOT drained"
    if (st.input_text or "").strip():
        return False, "unsent input-box text still present — capture incomplete"
    if st.bg_agents:
        live, why = _bg_agents_live(reg)
        if live:
            return False, (f"{live} background agent(s) still in flight — a /clear discards their "
                           f"work and it is NOT recoverable from the handoff ({why})")
        _log_pen_gate(f"background-agent guard cleared: {why}")
    return True, f"capture verified (handoff {fresh}; inbox drained; input box clear)"


def _do_reset(a: AgentCtx, reg: dict, outcome: Optional[dict] = None) -> tuple[bool, str]:
    """Full from-Mini reset: gate -> (checkpoint if stale) -> re-verify -> /clear
    (phantom-guarded, in-place, NEVER kill-session) -> verify -> boot -> PROVE it
    cleared. Any step failure ABORTS and pages loudly, leaving the body in a safe
    state.

    Returns (ok, detail). `ok` is True ONLY when the reset was PROVED by telemetry
    (a new cc_session_costs session_id with a collapsed token count) — never
    because send-keys returned 0.

    `outcome`: optional dict the caller passes in to COLLECT the structured result
    (same out-param convention as read_context_gauge's `dropped`), so a caller can
    branch on the three confirmation states WITHOUT string-sniffing `detail`:
      outcome["clear_submitted"] -> bool: Enter was pressed on /clear
      outcome["confirmation"]    -> CONFIRM_RESET | CONFIRM_NOT_RESET | CONFIRM_UNKNOWN
                                    (absent/None if we never got as far as clearing)
      outcome["confirm_detail"]  -> the evidence string
    Callers that pass nothing behave exactly as before."""
    if outcome is None:
        outcome = {}
    # CAI-500 condition 4 — NEVER-SELF, at the executor boundary (defense in
    # depth; run_executor already filters non-auto_reset bodies out). A body the
    # registry marks detect-only / self-compacting (orch-console) is refused even
    # on a direct call — it never resets itself.
    if not reg.get("auto_reset"):
        return False, f"SKIP: {a.agent} is a never-self / detect-only body — refusing reset"
    st = _pane_state(reg)
    if not st.reachable:
        return False, "SKIP: pane unreachable"
    if st.idle is not True:
        return False, f"SKIP: not idle (idle={st.idle}) — never reset a busy body"
    if st.authenticated is not True:
        return False, f"SKIP: not clearly authenticated (auth={st.authenticated})"

    # CAI-500 condition 1 — NO OWED ACTION in flight (inbox drained + no open
    # executor + idle). Any owed action -> DEFER (log + skip), never /clear.
    owed = _owed_action_in_flight(a, reg, st)
    if owed is not None:
        _log_pen_gate(f"red reset DEFERRED for {a.agent}: {owed}")
        return False, f"DEFER: owed action in flight — {owed} (no /clear this cycle)"

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

    # CAI-500 condition 2 — PROVABLE capture before the irreversible /clear. Prove
    # a fresh handoff landed, the inbox is STILL drained (re-read), and no unsent
    # input remains. If capture cannot be VERIFIED -> ABORT loudly, never /clear
    # (dead-man's-switch: body left INTACT with its saved state).
    ok_cap, cap_detail = _verify_capture_before_clear(reg, st2)
    if not ok_cap:
        _page_loud(f"🚨 ctx-watchdog ABORTED reset of {a.agent}: capture NOT verified ({cap_detail}). "
                   f"Body left INTACT — never /clear without provably-captured input. Manual reset needed.")
        _log_pen_gate(f"red reset ABORTED for {a.agent}: capture unverified ({cap_detail})")
        return False, f"ABORT: capture unverified ({cap_detail})"

    # Baseline for the ONLY evidence that can later prove a reset happened: this
    # body's current (session_id, latest_context_tokens). Taken as late as possible
    # — after every gate, immediately before the destructive step — so the reading
    # we compare against is the one the /clear is about to invalidate.
    before_fp = _session_fingerprint(a.agent)

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
    outcome["clear_submitted"] = True
    time.sleep(_POST_STEP_SETTLE_S)

    # 3. Verify after clear: alive, still authenticated, fresh prompt.
    st3 = _pane_state(reg)
    if not st3.reachable:
        _page_loud(f"🚨 ctx-watchdog: {a.agent} pane UNREACHABLE after a submitted /clear — may need "
                   f"break-glass. Handoff {fresh} was saved first.")
        return False, "ABORT: unreachable after clear"
    if st3.authenticated is False:
        _page_loud(f"🚨 ctx-watchdog: {a.agent} auth BROKE after a submitted /clear — needs break-glass "
                   f"login. Handoff {fresh} was saved first.")
        return False, "ABORT: auth broke after clear"

    # 4. Boot from the handoff. NOTE the wording: at this point we have submitted a
    #    /clear and observed nothing that PROVES it took effect — so nothing here
    #    may say "cleared". (The old copy said "cleared OK but the BOOT nudge
    #    failed", asserting the unverified half as fact.)
    if not _send_literal(reg, _boot_nudge(reg)) or not _send_key(reg, "Enter"):
        _page_loud(f"🚨 ctx-watchdog: {a.agent} — /clear was submitted but the BOOT nudge failed to send. "
                   f"Whether it cleared is UNVERIFIED; it may be sitting at an empty prompt. Check it and "
                   f"boot it by hand. Handoff {fresh}.")
        return False, "ABORT: boot nudge send failed"
    time.sleep(_POST_STEP_SETTLE_S)
    st4 = _pane_state(reg)
    # WEAK signal, and treated as one: a pane that looks busy / mentions "boot" is
    # consistent with a boot but proves nothing about the /clear.
    booting = (st4.idle is False) or ("boot" in (st4.raw or "").lower())

    # 5. PROVE it. Until this returns CONFIRM_RESET, the word "cleared" is not
    #    available to us — see _confirm_reset's docstring for the 2026-07-26 page
    #    this replaces.
    state, why = _confirm_reset(a.agent, before_fp)
    outcome["confirmation"] = state
    outcome["confirm_detail"] = why

    if state == CONFIRM_RESET:
        if not booting:
            # ASSERT what was observed (the reset is proved; the pane is quiet),
            # HEDGE nothing that is uncertain — the exact inversion of the old copy,
            # which asserted the unverified reset and hedged the observed quiet.
            _page_loud(f"⚠️ ctx-watchdog: {a.agent} reset CONFIRMED ({why}), but the pane shows NO activity "
                       f"after the boot nudge — check it started reading. Handoff {fresh}.")
        return True, f"reset CONFIRMED (handoff {fresh}; {why}; booting={booting})"

    if state == CONFIRM_NOT_RESET:
        _page_loud(f"🚨 ctx-watchdog: {a.agent} was NOT reset. A /clear was typed and submitted, but the "
                   f"telemetry proves the session never restarted ({why}). Treat {a.agent} as STILL FULL "
                   f"and reset it by hand. Handoff {fresh} is saved.")
        _log_pen_gate(f"red reset REFUTED for {a.agent}: {why}")
        return False, f"NOT RESET: /clear submitted but refuted by telemetry ({why})"

    # CONFIRM_UNKNOWN — the honest third state. Say plainly that it is unproven and
    # must be treated as NOT reset; do NOT dress lag up as either outcome.
    _page_loud(f"⚠️ ctx-watchdog: {a.agent} — a /clear was submitted but I could NOT confirm it took "
               f"effect ({why}). Treat {a.agent} as NOT reset until you have looked: if it is still full, "
               f"reset it by hand; if it is already fresh, this was only measurement lag. Handoff {fresh}.")
    _log_pen_gate(f"red reset UNCONFIRMED for {a.agent}: {why}")
    return False, f"UNCONFIRMED: /clear submitted, reset not provable ({why})"


def _run_checkpoint_for(a: AgentCtx, reg: dict, est: dict, now: float) -> tuple[bool, str]:
    """The WRITE-ONLY checkpoint half: nudge a body to write a fresh handoff.
    Zero-risk (never touches /clear). Deduped, gated on idle+auth. Returns
    (state_touched, result_line). Shared by amber bodies (any arm) and red bodies
    under arm=amber."""
    last_cp = float(est.get("checkpoint_at", 0) or 0)
    if (now - last_cp) < _CHECKPOINT_DEDUP_MIN * 60 and _fresh_handoff(reg):
        return False, f"{a.agent}: {a.level} — checkpoint deduped (fresh handoff exists)"
    st = _pane_state(reg)
    if not st.reachable:
        return False, f"{a.agent}: {a.level} — pane unreachable, skip"
    if st.idle is not True or st.authenticated is not True:
        return False, f"{a.agent}: {a.level} — not idle/authed (idle={st.idle} auth={st.authenticated}), skip"
    ok, detail = _do_checkpoint(a, reg, st)
    if ok:
        est["checkpoint_at"] = now
    else:
        _page_loud(f"⚠️ ctx-watchdog: {a.level} checkpoint of {a.agent} did not confirm a handoff ({detail}).")
    return ok, f"{a.agent}: {a.level} checkpoint {'OK' if ok else 'FAILED'} — {detail}"


def run_executor(rows: list[AgentCtx], arm_level: str = "red") -> list[str]:
    """ARMED entry point — GRANULAR (CAI-RESP-500). `arm_level` gates HOW far it acts:
      - 'off'   : no-op (should not be called, but fail-safe returns []).
      - 'amber' : the WRITE-ONLY half ONLY. Every ≥SOFT auto_reset body (amber OR
                  red) is checkpoint-nudged; a RED body is NEVER /clear'd here — it
                  is checkpoint-only + a dry-run note. `_do_reset` is never called.
      - 'red'   : both — amber -> checkpoint, red -> full /clear reset (still gated
                  live on idle+auth+fresh-handoff).
    De-duped via _EXEC_STATE_FILE; each body wrapped in a dead-man's-switch so a
    crash pages loudly instead of dying silent. Only touches auto_reset bodies
    (never self / orch-console). The invariant guarded HERE: _do_reset (the /clear)
    is reachable ONLY when arm_level == 'red'."""
    if arm_level not in ("amber", "red"):
        return []  # 'off' or anything unexpected — never execute
    # CAI-RESP-501: the self-healing EXECUTOR is the watchdog pen (iii) ACTING.
    # Only the fleet_health_lease holder runs it — fail-closed on positive
    # evidence a different live body holds the pen (so the SRE and a reclaiming
    # hub never both self-heal). Detection + the operator degrade-alert stay
    # UNGATED (a safety page must never be silenced by lease state).
    ok, why = fleet_health_lease.gate()
    if not ok:
        _log_pen_gate(f"executor DEFERRED (arm={arm_level}) :: {why}")
        return [f"executor DEFERRED — {why}"]
    state = _load_exec_state()
    now = time.time()
    results: list[str] = []
    for a in rows:
        reg = _AGENT_REGISTRY.get(a.agent)
        if not reg or not reg.get("auto_reset") or a.action == "ok":
            continue  # detect-only / self body / green — never auto-act
        est = state.get(a.agent, {})
        try:
            # The ONLY path that reaches _do_reset (/clear): a red body under the
            # explicit red arm. Everything else is the write-only checkpoint half.
            # `not a.stale` is load-bearing, not cosmetic: without it the
            # /clear decision can rest on a reading taken hours ago. Stale red
            # falls through to the write-only checkpoint half.
            do_destructive_reset = (a.level == "red" and arm_level == "red" and not a.stale)
            if do_destructive_reset:
                last_rs = float(est.get("reset_at", 0) or 0)
                if (now - last_rs) < _RESET_DEDUP_MIN * 60:
                    results.append(f"{a.agent}: red — reset deduped (last {int((now - last_rs) / 60)}m ago)")
                    continue
                outcome: dict = {}
                ok, detail = _do_reset(a, reg, outcome)
                confirmation = outcome.get("confirmation")
                if ok:
                    est["reset_at"] = now
                    est["checkpoint_at"] = now
                    state[a.agent] = est
                elif confirmation == CONFIRM_UNKNOWN:
                    # We submitted a /clear and cannot prove either way. Re-driving
                    # a second /clear+boot 10 minutes later at a body that DID reset
                    # would wipe its fresh boot — so hold the dedup timer (safe
                    # direction) even though this is reported as a FAILURE. The
                    # operator has been paged; the next cycle's telemetry will be
                    # unambiguous.
                    est["reset_at"] = now
                    state[a.agent] = est
                # ok is the authority for success (a caller/stub may not populate
                # `outcome` at all); the confirmation state only refines the FAILURE
                # wording so "refuted" and "unproven" never read the same.
                if ok:
                    label = "CONFIRMED"
                elif confirmation == CONFIRM_NOT_RESET:
                    label = "NOT-RESET (refuted by telemetry)"
                elif confirmation == CONFIRM_UNKNOWN:
                    label = "UNCONFIRMED (not proven either way)"
                else:
                    label = "SKIP/ABORT"
                results.append(f"{a.agent}: red reset {label} — {detail}")
            else:
                # amber body (any arm), OR red body under arm=amber -> WRITE-ONLY.
                touched, line = _run_checkpoint_for(a, reg, est, now)
                if touched:
                    state[a.agent] = est
                if a.level == "red":
                    line += "  (WOULD reset — red /clear executor UNARMED at arm=amber)"
                results.append(line)
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
# VALUE-FREEZE guard (2026-09-01): the age-stale check keys on ended_at, but a broken
# cost-writer can keep REFRESHING ended_at (row looks fresh) while latest_context_tokens
# is FROZEN — a zombie reading that isn't age-stale yet isn't a live measurement either
# (cc-orchestrator was frozen at 605790/60.6% since Aug 18, re-paging the operator hourly).
# If a body's ctx value hasn't MOVED for this long, it's frozen: refuse to page the % and
# surface the frozen gauge ONCE instead. A genuinely-active body rewrites its token count
# every few minutes, so an unchanged value across this window is a strong frozen signal.
# FROZEN threshold (Musa op#18830/18837): the ALWAYS-ON bodies (cai, orch-console, cc-orchestrator,
# the SRE) have LEGITIMATELY BURSTY cost-writers — the value jumps, then sits flat for 30-80 min,
# then jumps again (observed gaps up to ~80m). A 30-min threshold called every one of those normal
# flat stretches "frozen" and flap-paged the operator across ALL of them. Raise the window to 3h so
# a bursty gap is NOT mistaken for a broken writer, while a GENUINELY stuck writer (cc-orchestrator
# was frozen for DAYS at 605790 since Aug 18) is still caught — it stays flat far past 3h. This is
# the root fix; the re-page cooldown below is the secondary dampener. Env-overridable.
_CTX_FROZEN_S = int(os.environ.get("CTX_WD_FROZEN_MIN", "180")) * 60
# RE-PAGE cooldown (Musa op#18830): a body with a legitimately BURSTY cost-writer (cai jumps in
# steps — 664,111 -> 664,611 -> 676,560 -> 687,252 — with long flat gaps between) trips the freeze
# guard every gap. Without a cooldown the freeze/move state machine re-fires a fresh FROZEN->LIVE
# bookend on EACH burst = flapping operator spam (op#18830: FROZEN/LIVE pages every 30-60m all
# morning). Once the operator has been told a body's gauge is frozen, don't re-tell them for this
# long — even across the small burst-moves that reset the freeze episode. The FIRST frozen page
# (the true positive: a broken writer hiding real bloat) still fires immediately; only the
# repetition is damped. Env-tunable.
_FROZEN_REPAGE_S = int(os.environ.get("CTX_WD_FROZEN_REPAGE_H", "12")) * 3600
_LEVEL_RANK = {"green": 0, "amber": 1, "red": 2}

# --------------------------------------------------------------------------- #
# S2 self-recycle NUDGE tunables (autoscaler op#14098; params pinned by Nazim
# #25288/#25400/#25584). A self_compacts body is NUDGED to recycle ITSELF (an
# attributable bus row it can decline) with NO operator page — the decouple. The
# operator page is kept as a BACKSTOP that fires only if self-recycle demonstrably
# FAILS (the body ignores the nudge past a threshold), so "self-nudge, no page" is
# safe rather than a silent swallow. Every threshold here is a persisted CONFIG
# CONSTANT (env-overridable), never a live-memory timer — "a control that needs
# remembering is a sentence" (Nazim #24189). NOTE: NO pool-headroom gate on the
# nudge — a body approaching context death must be nudged regardless of pool crunch
# (Nazim #25584 caveat); the pool gate belongs on the discretionary WINDDOWN path.
_NUDGE_PCT = float(os.environ.get("CTX_WD_NUDGE_PCT", "0.70"))          # nudge at >=70% of window
_NUDGE_RISE_DELTA = int(os.environ.get("CTX_WD_NUDGE_RISE_DELTA", "10"))  # re-nudge only on a >=+10% rise (dedup, no storm)
_NUDGE_BACKSTOP_N = int(os.environ.get("CTX_WD_NUDGE_BACKSTOP_N", "3"))   # nudged N times, still not recycled -> operator backstop
_NUDGE_BACKSTOP_PCT = int(os.environ.get("CTX_WD_NUDGE_BACKSTOP_PCT", "95"))  # pinned near the CEILING despite nudging -> backstop
# NOTE the 95% default is deliberately near the ceiling, not the amber/red line: a self-compacting
# body (cai) legitimately RIDES high (~89-92%) and auto-compacts, so it is NOT "stuck" there —
# paging the operator on its normal band would re-create the very spam the autoscaler removes. The
# backstop is a safety net for a body PINNED at the wall ignoring nudges, not a compaction-policy
# nag. Env-tunable (CTX_WD_NUDGE_BACKSTOP_PCT) if a body's real ceiling differs.
_NUDGE_RECYCLE_DROP = int(os.environ.get("CTX_WD_NUDGE_RECYCLE_DROP", "15"))  # a >=15% drop = the body recycled/compacted -> episode over

# EXTERNAL-RECYCLE page line (CAI-RESP-1360): an externally-recycled body (the hub) does NOT
# self-recycle and its amber/steady-state is EXPECTED — so it pages the operator ONLY at the
# ceiling. Default 95% matches the existing backstop convention (_NUDGE_BACKSTOP_PCT); a body
# pinned here can't recover itself and genuinely needs an external recycle. Env-tunable.
_EXT_RECYCLE_PAGE_PCT = int(os.environ.get("CTX_WD_EXT_RECYCLE_PAGE_PCT", "95"))

# Operator-facing hub recycle command (CAI-1360) — PARAMETERIZED in ONE place (Nazim 36772).
# cai named reset_orch.sh, but that is the BARE ON-HOST reset and does NOT work from where the
# operator is; the hub RELOCATED (Studio -> VPS wingmen-core / 91.107.235.77, 2026-07-31) and the
# operator-facing recycle is the cross-host wrapper reset_hub_remote.sh (SSH -> VPS -> reset_orch.sh
# on the hub's home turf). A safety page must name the path that actually works. PINNED by
# orch-console (bus 37012/37014, verified wired to wingmen-core): CTX_WD_HUB_RECYCLE_CMD =
# `bash ~/wingmen/orchestrator/scripts/reset_hub_remote.sh`. Env-overridable so a future
# relocation can re-pin without a code edit.
_HUB_RECYCLE_REMEDIATION = os.environ.get(
    "CTX_WD_HUB_RECYCLE_CMD",
    "recycle the hub via its cross-host operator control — run "
    "`bash ~/wingmen/orchestrator/scripts/reset_hub_remote.sh` (SSHes to the relocated hub on the "
    "VPS wingmen-core / 91.107.235.77 and runs reset_orch.sh THERE) — NOT the bare on-host "
    "reset_orch.sh, which no longer reaches the hub",
)


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
    if os.environ.get("CTX_WD_ALERT_STDOUT") == "1" or _in_pytest():
        print("[ALERT-would-send]\n" + text + "\n")
        return
    try:
        subprocess.run(
            [str(_ORCH_DIR / "scripts" / "nazim_send.sh"), text],
            timeout=30, cwd=str(_ORCH_DIR),
        )
    except Exception as e:  # pragma: no cover — alert delivery must not crash the watchdog
        print(f"[ctx-health] alert send failed: {e}", file=sys.stderr)


def _alert_text(a: AgentCtx, reg: dict, nudged: bool = False) -> str:
    label = reg.get("label", a.agent)
    if reg.get("self_compacts"):
        # This body (Claude Code) AUTO-COMPACTS rather than fogging, so it needs no
        # OPERATOR reset — that much was right, and it is why this branch exists
        # (op-caught 2026-07-21: the generic 'reset before it fogs' copy kept telling
        # him to reset a body that heals itself).
        #
        # But the copy went on to call compaction "no action needed", and the operator
        # OVERRULED exactly that on 2026-08-15 (op#13418): "im worried about the auto
        # compaction because its not lossless." Compaction is the WORSE outcome, not a
        # clean valve — a body that hands off deliberately keeps what IT chose to keep;
        # a compacted body keeps what a summarizer chose. This line told him to relax
        # about the bodies that most need recycling, and it fired on the console all
        # night while it rode from 400k to 822k (op#13512: "we gonna wait till you
        # reach 100%?").
        #
        # Since 6eb9d01 there is a third option that did not exist when the old copy was
        # written: the body can recycle ITSELF, on its own fresh handoff, with no
        # operator button and no arm-sign. So the alert now names that as the action —
        # addressed to the BODY, with the operator merely informed.
        # no-fake-autopilot: say what actually happened. An alert claiming a nudge it did
        # not send stands the operator down on a body that was never told.
        tail = ("Nudged it on the bus to recycle itself; tell me if you'd rather it ride."
                if nudged else
                "I could NOT reach it on the bus to tell it — so nobody has. Say the word and I'll drive it by hand.")
        return (f"ℹ️ {label} at ~{a.pct}% — it should recycle itself; compaction is the fallback, not the fix.\n"
                f"{label} is at ~{a.pct}% of its window ({a.ctx_tokens:,} tokens). It won't fog — Claude Code "
                f"auto-compacts — but compaction is lossy: it keeps what a summarizer picked, not what the body "
                f"would have chosen. The better path is a deliberate recycle on its own fresh handoff "
                f"(scripts/self_recycle.sh), which needs no button from you. {tail}")
    try:
        from nervous_system.alert_format import format_alert
    except Exception:
        # A DELIVERED plain alert beats a pretty one that crashes into silence.
        head = "🚨 near-full — reset before it fogs" if a.level == "red" else "⚠️ context filling"
        tail = ("Reset it (checkpoint handoff -> in-place /clear -> boot) — say the word and I'll drive it."
                if a.level == "red" else
                "Checkpoint if you're mid-thread; I'll page again if it crosses the reset line.")
        return (f"{head}: {label} (~{a.pct}%)\n"
                f"{label} is at ~{a.pct}% of its 1M window ({a.ctx_tokens:,} tokens, {a.age_label}) "
                f"and does not auto-compact. {tail}")
    if a.level == "amber":
        return format_alert(
            icon="⚠️", title=f"{label} context filling (~{a.pct}%)",
            what=f"{label} is at ~{a.pct}% of its 1M window and does not auto-compact.",
            why="Left unchecked it degrades (gets foggy/slow) instead of resetting cleanly.",
            do="No action yet — I'll page again if it crosses the reset line. Checkpoint if you're mid-thread.",
            detail=f"{a.agent}: {a.ctx_tokens:,} tokens, {a.age_label}. amber≥{_SOFT:.0%}.",
        )
    return format_alert(
        icon="🚨", title=f"{label} near-full (~{a.pct}%) — reset before it fogs",
        what=f"{label} is at ~{a.pct}% of its 1M window — the point where a non-compacting body starts degrading.",
        why="A fogged body gives slow/incoherent answers and can post stale rulings; catching it here beats you noticing it.",
        do="Reset it via the from-Mini playbook (checkpoint handoff -> in-place /clear -> boot). Say the word and I'll drive it.",
        detail=f"{a.agent}: {a.ctx_tokens:,} tokens, {a.age_label}. red≥{_HARD:.0%}.",
    )


def _backstop_alert_text(a: AgentCtx, reg: dict, count: int) -> str:
    """The BACKSTOP operator page (S2). Distinct from the soft self_compacts _alert_text:
    this fires only when self-recycle has demonstrably FAILED — the body was nudged and did
    not act, so the operator MUST know (a suppressed page that read as 'all healthy' would
    be the swallow the decouple must avoid)."""
    label = reg.get("label", a.agent)
    how = (f"nudged {count}x and it has NOT recycled" if count >= _NUDGE_BACKSTOP_N
           else f"climbed to ~{a.pct}% despite the self-recycle nudge")
    return (f"🚨 {label} — SELF-RECYCLE NOT HAPPENING (backstop): {label} is at ~{a.pct}% "
            f"({a.ctx_tokens:,} tokens); I {how}. A self-compacting body that ignores the nudge "
            f"past the line is either mid-a-long-commitment or stuck — either way you should know. "
            f"Check it / decide whether to drive a reset by hand. This is the BACKSTOP; the routine "
            f"self-recycle nudge is silent by design, so this page means the quiet path did not work.")


def _handle_self_recycle(a: AgentCtx, reg: dict, state: dict, now: float) -> Optional[str]:
    """S2 self-recycle NUDGE path for a self_compacts body. NUDGE the body to recycle ITSELF
    (an attributable bus row it can decline) with NO operator page — the decouple. Keep the
    operator page as a BACKSTOP that fires only when self-recycle demonstrably FAILED (nudged
    N times without recycling, OR climbed to the danger line). Mutates state[a.agent]; returns
    a short status string for `fired`/logging, or None if nothing happened this cycle."""
    prev = state.get(a.agent, {})
    nudge_pct = float(prev.get("nudge_pct", 0) or 0)
    count = int(prev.get("nudge_count", 0) or 0)
    backstop_paged = bool(prev.get("backstop_paged", False))

    # Episode over: recovered below the nudge line, OR a sharp ctx drop (recycled/compacted).
    below = a.pct < _NUDGE_PCT * 100
    recycled = bool(prev) and (nudge_pct - a.pct) >= _NUDGE_RECYCLE_DROP
    if below or recycled:
        state.pop(a.agent, None)
        return None

    # Nudge on the FIRST crossing and thereafter ONLY on a >=+10% rise (dedup — no nudge-storm,
    # 2026-07-08 lesson). A failed send neither counts nor advances the mark (no-fake-autopilot):
    # it is retried next cycle, and the pct-backstop below still surfaces a dangerously-high body.
    seen_before = "nudge_count" in prev  # processed by THIS path in a prior cycle (not old-schema/first contact)
    sent = False
    attempted = False
    if (not prev) or (a.pct - nudge_pct) >= _NUDGE_RISE_DELTA:
        attempted = True
        if _bus_nudge_self_recycle(a.agent, a.pct):
            count += 1
            nudge_pct = a.pct
            sent = True
    nudge_failed = attempted and not sent

    # BACKSTOP triggers — each means self-recycle has demonstrably FAILED, never "first contact
    # with a high body" (cai legitimately rides near 90%; nudging it this cycle is NOT a failure).
    #   (1) count >= N  : we successfully nudged N times and it still has not recycled.
    #   (2) danger pct   : it is at/above the danger line AND either we saw it (and nudged it) in a
    #       PRIOR cycle and it did not recycle, OR we cannot even deliver the nudge now (channel
    #       broken) — a dangerously-high body nobody can reach must surface immediately.
    status: Optional[str] = "self-nudged" if sent else None
    backstop = (count >= _NUDGE_BACKSTOP_N) or \
               (a.pct >= _NUDGE_BACKSTOP_PCT and (seen_before or nudge_failed))
    if backstop and not backstop_paged:
        _send_alert(_backstop_alert_text(a, reg, count))
        backstop_paged = True
        status = f"backstop-paged({count}x,{a.pct}%)"
        # A suppressed page must never read as 'all healthy' — log the escalation loudly.
        print(f"[ctx-health] BACKSTOP: {a.agent} self-recycle FAILED — nudged {count}x, "
              f"now {a.pct}% -> operator paged", file=sys.stderr)

    state[a.agent] = {"level": a.level, "nudge_pct": nudge_pct,
                      "nudge_count": count, "backstop_paged": backstop_paged}
    return status


def _external_recycle_alert_text(a: AgentCtx, reg: dict) -> str:
    """Operator page for an EXTERNALLY-recycled body (the hub) at the ceiling (CAI-1360). Names
    the REAL remediation (an external recycle, via the parameterized _HUB_RECYCLE_REMEDIATION)
    and NEVER self-recycle language — this body does NOT self-recycle, so a self-recycle nudge
    would be un-actionable. Steady-state amber is expected and never reaches this text."""
    label = reg.get("label", a.agent)
    return (f"🚨 {label} — needs an EXTERNAL recycle (~{a.pct}%): {label} is at ~{a.pct}% of its "
            f"1M window ({a.ctx_tokens:,} tokens). It is long-lived and harness-compacted and does "
            f"NOT self-recycle — {_HUB_RECYCLE_REMEDIATION}. A multi-week session is normal; this "
            f"fires only at the ceiling (>= {_EXT_RECYCLE_PAGE_PCT}%), not on steady-state amber.")


def _handle_external_recycle(a: AgentCtx, reg: dict, state: dict, now: float) -> Optional[str]:
    """CAI-RESP-1360 external-recycle profile for a long-lived, EXTERNALLY-recycled singleton (the
    hub). Amber/steady-state is EXPECTED, not degradation -> NO operator page. Page ONLY at the
    ceiling (>= _EXT_RECYCLE_PAGE_PCT), latched once per episode and red-style re-nagged so a body
    pinned at the wall keeps surfacing until recovered. The frozen/non-advancing case is handled
    earlier by the value-freeze guard (which pages with the same external-recycle remediation).
    Mutates state[a.agent]; returns a status string for `fired`, or None."""
    if a.pct < _EXT_RECYCLE_PAGE_PCT:
        state.pop(a.agent, None)  # steady-state (incl. amber) is expected -> no page, clear episode
        return None
    prev = state.get(a.agent, {})
    paged = bool(prev.get("ext_paged", False))
    prev_ts = float(prev.get("alerted_at", 0) or 0)
    renag = (now - prev_ts) >= _RENAG_MIN * 60
    if not paged or renag:
        _send_alert(_external_recycle_alert_text(a, reg))
        state[a.agent] = {"level": a.level, "ext_paged": True, "alerted_at": now}
        # A ceiling page on an externally-recycled body is a genuine escalation — log it loudly.
        print(f"[ctx-health] EXTERNAL-RECYCLE page: {a.agent} at {a.pct}% "
              f"(ceiling >= {_EXT_RECYCLE_PAGE_PCT}%) -> operator paged", file=sys.stderr)
        return f"ext-recycle-paged({a.pct}%)"
    state[a.agent] = {"level": a.level, "ext_paged": True, "alerted_at": prev_ts}
    return None


def _frozen_gauge_should_page(a: AgentCtx, reg: dict) -> bool:
    """IDLE-vs-ACTIVE gate on the frozen-gauge page (Nazim 36318->36319->36707; op#18601/18830/18837).

    A frozen ctx gauge is a broken-writer zombie HIDING REAL BLOAT only when the body is actually
    WORKING. ROOT INSIGHT (proven 2026-09-02 in the op#18830/18837 writer dig): for a LOCAL body the
    cost-writer advances the gauge on ANY active turn, so a genuinely-frozen gauge IMPLIES the body is
    idle — and an idle body's flat gauge is legitimate at EVERY band (green or amber+), because an idle
    body accumulates no new/hidden context. So CONFIRMED-idle must never page, at any band (the earlier
    'amber+ always pages' rule false-paged orch-console frozen-amber overnight — op#18837). Gate:
      - pane IDLE (idle is True): CONFIRMED idle -> gauge legitimately static at ANY band -> SUPPRESS
        (kills both op#18601 cc-quality-@13%-green and op#18837 orch-console-amber-overnight false pages).
      - pane BUSY (idle is False): actively producing + frozen gauge = real zombie -> PAGE, any band
        (the low-%-then-bloating true positive, Nazim 36319).
      - pane UNREACHABLE (idle is None: a cross-host body the Mini cannot see, e.g. the hub on Studio):
        fall back to the value BAND -> PAGE at >= SOFT/amber (the hub-@61% zombie, genuinely stuck 53h),
        SUPPRESS at green (nothing high hidden, and can't confirm active).
    A suppressed page is LOGGED LOUDLY (charter #1: a silently-dropped page is worse than none) and
    leaves rec["paged"] False so the next cycle re-evaluates — delayed if the body turns active, never
    permanently swallowed.
    """
    idle = _agent_is_idle(reg)
    if idle is False:  # actively mid-turn ("esc to interrupt") + frozen -> real zombie
        return True
    if idle is None and _LEVEL_RANK.get(a.level, 0) >= _LEVEL_RANK["amber"]:
        return True  # cross-host/unreachable + high frozen value -> the hub-@61% zombie still pages
    why = ("body CONFIRMED idle (pane not busy) — idle at any band hides no bloat" if idle is True
           else f"pane unreachable and band green (<{int(_SOFT * 100)}%) — nothing high hidden")
    print(f"[ctx-health] frozen-gauge page SUPPRESSED — {a.agent} static at "
          f"{a.ctx_tokens:,} tokens ({a.pct}%): {why}; not paging the operator "
          f"(idle-vs-active gate; op#18601/18837).", file=sys.stderr)
    return False


def run_alerts(rows: list[AgentCtx]) -> list[str]:
    """Handle amber/red bodies. A self_compacts body is NUDGED to self-recycle (no operator
    page) with an operator BACKSTOP if it ignores the nudge (S2). A non-self-compacting
    alerts body still pages the operator on a level rise/re-nag (e.g. the hub). Returns the
    list of agent names actioned this cycle (for logging)."""
    import time
    state = _load_state()
    fz = state.setdefault("__ctx_freeze__", {})  # per-agent value-freeze tracking (not an agent key)
    now = time.time()
    fired: list[str] = []
    for a in rows:
        reg = _AGENT_REGISTRY.get(a.agent)
        # Processed if it takes a plain operator page OR is a self-recyclable body (which may
        # have alerts:False, e.g. the SRE — self-nudge only, never a plain page).
        if not reg or not (reg.get("alerts") or reg.get("self_compacts") or reg.get("external_recycle")):
            continue
        if a.stale:
            # (freeze tracking left untouched here: a frozen value stays frozen while
            # age-stale, so `since` should keep counting, not reset.)
            # STALE = the body has not written telemetry in > _STALE_S: it is offline,
            # dead, or mid-a-very-long-turn — this ctx% is its LAST-KNOWN, not current
            # (ended_at is a live-updated last-activity stamp, so a LIVE body stays
            # fresh; only one that STOPPED writing goes stale). Never page/nudge on
            # non-current telemetry (a downed cc-quality still reads 95% for hours) —
            # consistent with the reset path, which already refuses a stale body. A
            # genuinely-bloated LIVE body writes a fresh row and is actioned then.
            state.pop(a.agent, None)
            continue
        # --- VALUE-FREEZE guard: a fresh-looking row (not age-stale) whose ctx value has
        #     not MOVED for _CTX_FROZEN_S is a zombie reading (broken cost-writer refreshing
        #     ended_at while latest_context_tokens is frozen), NOT climbing context. Refuse
        #     the %-page; surface the frozen gauge ONCE (deduped), bookend when it recovers. ---
        rec = fz.get(a.agent)
        if rec and rec.get("tokens") == a.ctx_tokens:
            frozen_for = now - float(rec.get("since", now))
        else:
            was_paged = bool(rec and rec.get("paged"))
            # Carry last_page_ts across the value-move: it gates FROZEN RE-pages so a bursty
            # writer can't re-fire a fresh FROZEN every burst (op#18830 flapping).
            last_page_ts = float(rec.get("last_page_ts", 0)) if rec else 0.0
            fz[a.agent] = {"tokens": a.ctx_tokens, "since": now, "paged": False,
                           "last_page_ts": last_page_ts}
            rec = fz[a.agent]
            frozen_for = 0.0
            if was_paged:  # value moved after a frozen-page -> bookend an all-clear
                _send_alert(f"✅ ctx gauge for {a.agent} is LIVE again (value moved to "
                            f"{a.ctx_tokens:,} tokens) — real context is visible.")
                fired.append(a.agent)
        if frozen_for >= _CTX_FROZEN_S:
            state.pop(a.agent, None)  # do NOT also run the level-rise pager on a zombie reading
            if not rec.get("paged") and reg.get("alerts") and _frozen_gauge_should_page(a, reg):
                since_last = now - float(rec.get("last_page_ts", 0) or 0)
                if since_last >= _FROZEN_REPAGE_S:
                    # CAI-1360: for an externally-recycled body, name the REAL remediation — but
                    # keep verify-FIRST (Nazim 36772): a frozen GAUGE can be a dead cost-writer,
                    # not a stuck BODY, so verify at source before recycling, never blind-recycle.
                    remediation = (
                        f" If the body itself (not just the writer) is stuck, recover it with an "
                        f"external recycle: {_HUB_RECYCLE_REMEDIATION}."
                        if reg.get("external_recycle") else "")
                    _send_alert(
                        f"⚠️ ctx gauge FROZEN: {a.agent} stuck at {a.ctx_tokens:,} tokens "
                        f"({a.pct}%) for ~{int(frozen_for // 60)}m while its row keeps refreshing "
                        f"— a ZOMBIE reading, NOT climbing context. Real bloat for {a.agent} is NOT "
                        f"visible; its session cost-writer is likely stale. Verify the body via "
                        f"lease/pane, not this gauge.{remediation}")
                    rec["paged"] = True
                    rec["last_page_ts"] = now
                    fired.append(a.agent)
                else:
                    # Genuinely-frozen gauge, but the operator was told <_FROZEN_REPAGE_S ago and a
                    # bursty writer keeps re-tripping the guard — suppress the RE-page (LOUD log,
                    # charter #1), leaving paged False so it re-evaluates. op#18830 flapping fix.
                    print(f"[ctx-health] frozen-gauge RE-page suppressed for {a.agent} — last "
                          f"frozen-paged {int(since_last // 60)}m ago, within the "
                          f"{_FROZEN_REPAGE_S // 3600}h re-page cooldown (bursty writer keeps "
                          f"re-tripping; Musa op#18830 flapping fix). Gauge still frozen — verify "
                          f"via lease/pane if you need the real number.", file=sys.stderr)
            continue
        if reg.get("self_compacts"):
            # S2: NUDGE it to recycle itself; page the operator only as the backstop.
            status = _handle_self_recycle(a, reg, state, now)
            if status:
                fired.append(a.agent)
            continue
        if reg.get("external_recycle"):
            # CAI-1360: long-lived, externally-recycled body (the hub). Amber/steady-state is
            # EXPECTED -> no page; page only at the ceiling with external-recycle remediation.
            status = _handle_external_recycle(a, reg, state, now)
            if status:
                fired.append(a.agent)
            continue
        # --- plain operator-page path: a non-self-compacting alerts body (e.g. the hub) ---
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
            _send_alert(_alert_text(a, reg, nudged=False))
            state[a.agent] = {"level": a.level, "alerted_at": now}
            fired.append(a.agent)
        else:
            # Hold: keep the level current but preserve alerted_at so the red re-nag
            # timer keeps counting from the last real page (not from every cycle).
            state[a.agent] = {"level": a.level, "alerted_at": prev_ts}
    _save_state(state)
    return fired


def _resolve_arm_level(args) -> str:
    """off | amber | red. --arm flag wins; else env CTX_WD_ARM; else off.
    Granular (CAI-RESP-500): amber = write-only checkpoint half; red = also the
    destructive /clear reset. `--arm` with no value means red (legacy: both)."""
    if getattr(args, "arm", None) is not None:
        return args.arm
    env = (os.environ.get("CTX_WD_ARM") or "off").strip().lower()
    return env if env in ("amber", "red") else "off"


def _clear_gauge_unreachable_streak(alert: bool = False) -> None:
    """A successful gauge read clears any prior transient-unreachable streak. If a
    LOUD 'BLIND — NOT guarding' page actually went out for that streak, bookend it
    with a RECOVERY all-clear so the operator gets closure instead of a dangling
    alarm — a real alarm earns a real all-clear. A soft single-skip that recovered
    never paged, so it stays silent (nothing to close)."""
    st = _load_exec_state()
    streak = int(st.get("gauge_unreachable_streak", 0) or 0)
    paged = bool(st.get("gauge_unreachable_paged"))
    if not streak and not paged:
        return
    st["gauge_unreachable_streak"] = 0
    st.pop("gauge_unreachable_paged", None)
    _save_exec_state(st)
    if paged and alert:
        _page_loud(f"✅ Context watchdog RECOVERED — guarding again after {streak} "
                   f"run(s) blind (~{streak * _GAUGE_TICK_MIN}m). Substrate gauge reachable.")


def _handle_gauge_unreachable(err: "GaugeUnreachable", args) -> int:
    """The substrate gauge was unreachable after retries THIS run. A transient single
    skip self-heals on the next StartInterval tick (soft: log, clean exit, no page).
    A PERSISTENT streak means the watchdog is genuinely blind and MUST page loud — the
    dead-man's-switch stays intact; we just don't cry 'CRASHED' at one DNS blip that
    self-recovers in <=10 min."""
    st = _load_exec_state()
    streak = int(st.get("gauge_unreachable_streak", 0)) + 1
    st["gauge_unreachable_streak"] = streak
    print(f"[ctx-health] gauge DB unreachable after {_GAUGE_CONNECT_ATTEMPTS} attempts "
          f"({err}); skipped this run (streak={streak}), will retry next tick "
          f"(~{_GAUGE_TICK_MIN}m). No classification this run.", file=sys.stderr)
    if args.json:
        print(json.dumps({"rows": [], "exec": [], "arm_level": _resolve_arm_level(args),
                          "gauge_unreachable": True, "unreachable_streak": streak}, indent=2))
    # Loud ONLY when persistent, and only in --alert mode (a dry-run never pages).
    if (args.alert and streak >= _GAUGE_UNREACHABLE_LOUD_AFTER
            and (streak == _GAUGE_UNREACHABLE_LOUD_AFTER or streak % 6 == 0)):
        _page_loud(
            f"🐛 Context watchdog BLIND for {streak} consecutive runs "
            f"(~{streak * _GAUGE_TICK_MIN}m): substrate gauge UNREACHABLE ({err}). "
            f"NOT guarding context bloat — check the Mini's DB/DNS path to the pooler.")
        # Record that a loud page went out, so a later recovery can bookend it with
        # an all-clear instead of clearing the alarm silently.
        st["gauge_unreachable_paged"] = True
    _save_exec_state(st)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--arm", nargs="?", const="red", choices=["amber", "red"], default=None,
                    help="arm the executor GRANULARLY: --arm=amber = WRITE-ONLY checkpoint half "
                         "(zero-risk, never /clear); --arm or --arm=red = also the destructive "
                         "/clear+boot reset (gated on idle+auth+fresh-handoff). Absent -> off "
                         "(dry-run). Overrides env CTX_WD_ARM.")
    ap.add_argument("--alert", action="store_true",
                    help="page the operator (nazim-console) on amber/red for alert-enabled bodies")
    args = ap.parse_args()
    arm_level = _resolve_arm_level(args)

    # CAI-RESP-501 hard boundary (a): cc-fleet-health has NO singleton-body reset
    # authority. The destructive red /clear is a SEPARATE, CAI-500-gated executor
    # the SRE never drives; only the write-only amber checkpoint half is ever
    # armed for it. Fail-closed (loud crash -> the __main__ dead-man page).
    fleet_health_boundaries.assert_no_sre_red_reset(arm_level)

    dropped: list = []
    try:
        rows = read_context_gauge(dropped)
    except GaugeUnreachable as _ge:
        # Transient substrate blip: soft-skip this run (self-heals next tick); page
        # loud only on a PERSISTENT streak. Never the raw-crash 'watchdog CRASHED'.
        return _handle_gauge_unreachable(_ge, args)
    _clear_gauge_unreachable_streak(args.alert)  # good read: clear streak (+ all-clear if it loud-paged)
    if args.json:
        out = [{**asdict(r), "plan": plan_reset(r, arm_level=arm_level)} for r in rows]
        exec_results = run_executor(rows, arm_level) if arm_level != "off" else []
        print(json.dumps({
            "rows": out, "exec": exec_results, "arm_level": arm_level,
            "unmeasurable": [
                {"agent": i, "ctx_tokens": c, "assumed_window": w, "reason": why}
                for i, c, w, why in dropped
            ],
        }, indent=2))
        if args.alert:
            run_alerts(rows)
        return 0

    reset_mode = {
        "off": "DRY-RUN (detect+plan only)",
        "amber": "ARMED=AMBER — write-only checkpoint half (NO /clear)",
        "red": "ARMED=RED — LIVE checkpoint + destructive /clear reset",
    }[arm_level]
    alert_mode = "ON" if args.alert else "off"
    print(f"[ctx-health] soft={_SOFT:.0%} hard={_HARD:.0%} arm={arm_level} reset={reset_mode} alert={alert_mode}")
    if not rows:
        print("[ctx-health] no context telemetry (writer running?)")
        return 0
    for r in rows:
        win = _AGENT_REGISTRY.get(r.agent, {}).get("window", _CTX_WINDOW)
        mark = "STALE" if r.stale else "     "
        print(f"  {r.level:5} {mark} {r.agent:18} {r.pct:3}% of {win//1000}K  ({r.ctx_tokens:>9,})  "
              f"{r.age_label}  -> {plan_reset(r, arm_level=arm_level)}")
    # Report what we could NOT classify. Absence used to be indistinguishable
    # from "not monitored"; a gauge that cannot say "I failed here" is not a gauge.
    for ident, ctx, win, why in dropped:
        print(f"  UNMEASURABLE  {ident:18} {ctx:>9,} tokens vs assumed window {win//1000}K — {why} "
              f"(NOT classified; fix the window or the writer)")
    if arm_level != "off":
        print(f"[ctx-health] ARMED={arm_level}: running executor "
              f"({'checkpoint-only, no /clear' if arm_level == 'amber' else 'checkpoint + reset'}, gated + deduped)...")
        for line in run_executor(rows, arm_level):
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
