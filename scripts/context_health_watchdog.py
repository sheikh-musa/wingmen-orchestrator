#!/usr/bin/env python3
"""context_health_watchdog.py — DORMANT context auto-reset watchdog (op#4676).

Closes the loop the gauge left open: the cc_session_costs gauge DETECTS context
bloat, but nothing acts on it. This watchdog reads the gauge and — when ARMED —
orchestrates the safe reset-from-handoff procedure (checkpoint -> /clear -> boot),
the same "reset-from-Mini" playbook a human runs by hand today.

  ┌─ SAFETY / STATUS ─────────────────────────────────────────────────────────┐
  │ BUILT, NOT RUN (op#4676: "build it but dont run it yet").                  │
  │ - NOT loaded into launchd (no active plist).                              │
  │ - Default mode is DRY-RUN: it DETECTS + LOGS what it *would* do and       │
  │   NEVER touches an agent. The reset path only executes under --arm AND    │
  │   after per-agent idle + fresh-handoff verification both pass.            │
  │ Auto-triggering /clear on a live agent can lose work if it misfires, so   │
  │ the reset is hard-gated on: agent IDLE + a FRESH handoff on disk.         │
  └────────────────────────────────────────────────────────────────────────────┘

Thresholds (% of the model context window, default 1M):
  green  < SOFT (60%)
  amber  SOFT..HARD        -> nudge the agent to CHECKPOINT (write a handoff), no reset
  red    >= HARD (80%)     -> reset-ELIGIBLE (armed + gates -> reset; else log)

Reset sequence (ARMED only — the reset-from-Mini playbook, each step gated):
  1. agent pane must be IDLE  (footer NOT "esc to interrupt")
  2. a FRESH handoff must exist (mtime within HANDOFF_MAX_AGE_MIN); else nudge to
     checkpoint and SKIP the reset this cycle — never reset without a saved state
  3. /clear via tmux (type -> verify text present -> Enter phantom-guard)
  4. boot from the handoff (prompt referencing the handoff path)
Never /clear a non-idle agent or one lacking a fresh handoff.

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
import shutil
import subprocess
import sys
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
_AGENT_REGISTRY = {
    "cc-orchestrator": {"host": "mac-studio", "tmux": "orch",  "handoff_glob": "reports/session-handoff-*.md", "window": 1_000_000, "alerts": True,  "label": "The hub (orch, Studio)"},
    "cai":             {"host": "mac-studio", "tmux": "cai",   "handoff_glob": "reports/cai-handoff-*.md",     "window": 1_000_000, "alerts": True,  "label": "cai (Studio)"},
    "orch-console":    {"host": "self",        "tmux": "nazim", "handoff_glob": "reports/nazim-handoff-*.md",  "window":   200_000, "alerts": False, "label": "Nazim (console, Mini)"},
}


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


def _agent_is_idle(reg: dict) -> Optional[bool]:
    """True if the agent pane is idle (not running). None if unknown/unreachable.

    An agent showing 'esc to interrupt' in its footer is mid-task -> NEVER reset.
    """
    tmux = reg["tmux"]
    tbin = _tmux_bin(reg["host"])
    cap = (
        f'{tbin} capture-pane -t {tmux} -p 2>/dev/null | tail -3'
    )
    cmd = cap if reg["host"] == "self" else f"ssh -o ConnectTimeout=8 Musa@{reg['host']} '{cap}'"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    pane = r.stdout
    if "esc to interrupt" in pane:
        return False   # working
    return True        # idle prompt


def _fresh_handoff(reg: dict) -> Optional[Path]:
    """Newest handoff matching the glob if within HANDOFF_MAX_AGE_MIN, else None.

    Only checked for host=self here; cross-host handoff freshness would be
    verified over ssh in a fuller impl. Kept conservative: unknown -> None -> SKIP.
    """
    if reg["host"] != "self":
        return None  # conservative: don't claim freshness we can't verify -> no reset
    import time
    newest, newest_m = None, 0.0
    for p in _ORCH_DIR.glob(reg["handoff_glob"]):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_m:
            newest, newest_m = p, m
    if newest and (time.time() - newest_m) <= _HANDOFF_MAX_AGE_MIN * 60:
        return newest
    return None


def plan_reset(a: AgentCtx, armed: bool) -> str:
    """Return the decision string. When NOT armed, always 'DRY-RUN: would <x>'.

    Even when armed, this only DECIDES; actual /clear+boot execution is left to a
    reviewed, separately-enabled executor (deliberately not wired in this build).
    """
    reg = _AGENT_REGISTRY.get(a.agent)
    if a.action == "ok":
        return "ok"
    if a.action == "checkpoint-nudge":
        return f"{'WOULD ' if not armed else ''}nudge {a.agent} to checkpoint (amber {a.pct}%)"
    # reset-eligible (red)
    if not reg:
        return f"detect-only: {a.agent} red {a.pct}% but not in reset registry"
    idle = _agent_is_idle(reg) if armed else None
    handoff = _fresh_handoff(reg) if armed else None
    gate = []
    if armed:
        if idle is not True:
            gate.append(f"NOT-IDLE({idle})")
        if handoff is None:
            gate.append("NO-FRESH-HANDOFF")
    if not armed:
        return f"DRY-RUN: would reset {a.agent} (red {a.pct}%) IF idle+fresh-handoff"
    if gate:
        return f"SKIP reset {a.agent} — gate failed: {','.join(gate)} (nudge-to-checkpoint instead)"
    # Gates passed + armed. Execution intentionally NOT performed here — the
    # actual /clear+boot is left to a reviewed executor to avoid a first-run
    # foot-gun. Log the ready state loudly.
    return f"RESET-READY {a.agent} (red {a.pct}%, idle+handoff {handoff.name}) — executor not wired (build-not-run)"


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
        print(json.dumps([{**asdict(r), "plan": plan_reset(r, args.arm)} for r in rows], indent=2))
        if args.alert:
            run_alerts(rows)
        return 0

    reset_mode = "ARMED" if args.arm else "DRY-RUN (build-not-run)"
    alert_mode = "ON" if args.alert else "off"
    print(f"[ctx-health] soft={_SOFT:.0%} hard={_HARD:.0%} reset={reset_mode} alert={alert_mode}")
    if not rows:
        print("[ctx-health] no context telemetry (writer running?)")
        return 0
    for r in rows:
        win = _AGENT_REGISTRY.get(r.agent, {}).get("window", _CTX_WINDOW)
        print(f"  {r.level:5} {r.agent:16} {r.pct:3}% of {win//1000}K  ({r.ctx_tokens:>9,})  age={r.age_s}s  -> {plan_reset(r, args.arm)}")
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
