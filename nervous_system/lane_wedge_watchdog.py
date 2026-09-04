#!/usr/bin/env python3
"""lane_wedge_watchdog.py — detect + recover the fleet "idle-composer wedge".

THE PROBLEM it solves (2026-07-29 incident, the governance node): `cai` sat
IDLE-WEDGED for ~6 hours undetected. Its process was up and it HELD its lease, so
every existing liveness check read it healthy — but it had STOPPED DRAINING its
bus inbox and its tmux composer was frozen on a dim-ghost that no Enter could
submit. A live money-path grant stalled until a human manually nudged it. Nothing
in the fleet catches this class:
  * the context watchdog acts on token bloat,
  * the priority-SLA watchdog acts on a message crossing its SLA (but a lease-
    holding, process-alive agent looks fine to it until the SLA fires, and it
    cannot tell "busy elsewhere" from "wedged"),
  * the lane/fleet-stall watchdogs act on DEAD processes.
A wedged agent is ALIVE, holds its lease, has plenty of context, and is simply not
consuming its inbox. This watchdog is the missing detector.

THE WEDGE SIGNATURE (all three must hold — see `evaluate`):
  A. SUBSTRATE (host-agnostic, no tmux): the agent has unread bus rows whose
     oldest actionable one is older than UNREAD_MIN_AGE_SEC, AND
  B. it has not itself written to the bus within QUIET_SEC (a live-working agent
     writes; a wedged one is silent), AND
  C. its tmux composer is EMPTY or a DIM GHOST — no REAL staged work — so an
     auto-nudge is safe (it appends to nothing). REAL non-dim staged text means
     the agent has its OWN draft: that is NOT clobbered by an auto-nudge — we
     ALERT instead.
A healthy-idle agent (empty inbox — nothing to do) has NO unread piling and is
never flagged. Signal A is the PRIMARY trigger and needs no tmux access, so it
works even for the VPS hub with no SSH; Signal B only CONFIRMS it is safe to
auto-nudge, and is delegated to the sanctioned nudge tool for singletons (which
carry their own ghost/real guard).

  ┌─ SAFETY / STATUS ─────────────────────────────────────────────────────────┐
  │ SHIPS DETECT-ONLY. Recovery is a staged-arm ladder; default = the SAFEST.  │
  │  - detect-only (DEFAULT): classify + log; with --alert, PAGE the operator  │
  │    once per wedge. Touches NO agent.                                        │
  │  - --arm=nudge : ALSO fire the ghost-aware count-only nudge (nudge_cai.sh / │
  │    lane_nudge.sh / a substrate-tmux nudge for the hub), once per episode.   │
  │  - --arm (=escalate): ALSO, if still wedged after a nudge, reset_lane.sh    │
  │    for LANES (git-clean-guarded); for SINGLETONS (cai/hub/SRE) PAGE — never │
  │    auto-reset a singleton body.                                             │
  │ Recovery ACTIONS are lease-gated (fleet_health_lease, CAI-RESP-501): fail-  │
  │ CLOSED for a known non-holder. Detection + the operator page are UNGATED —  │
  │ a safety page is never silenced by lease state.                            │
  │ NEVER auto-submits REAL staged text (the 07-04 084 money-migration phantom- │
  │ injection class lane_watchdog.py learned): real text -> ALERT only.        │
  └────────────────────────────────────────────────────────────────────────────┘

DEAD-MAN'S-SWITCH (feedback_monitors_need_deadmans_switch — a silently-dead guard
is worse than none): every scan writes a heartbeat; if the gap since the last one
exceeds DEADMAN_GAP_SEC the operator is paged (the watchdog was blind). Any
unhandled exception pages loudly via the dependency-free subprocess path.

Mirrors the sibling watchdogs (context_health_watchdog / priority_sla_watchdog):
stdlib + psycopg via DATABASE_URL/SUPABASE_DB_URL in .env, logs/ file logging, a
JSON state file across scans, fleet_health_lease action-gating, nazim_send.sh for
operator pages. Reuses scripts/lib/composer_capture.sh (the fleet's ONE "dim ghost
vs real staged text" definition) via shell-out — never a reimplementation.

TWO-TMUX NOTE: Mini lanes live on the /usr/local/bin/tmux server; /opt/homebrew's
tmux sees NONE of them (different server) — reference_mini_tmux_two_binaries_socket.
Resolve the binary the way reset_lane.sh does; override via LANE_WEDGE_TMUX.

Usage:
    lane_wedge_watchdog.py                 # detect-only, one scan (ships this)
    lane_wedge_watchdog.py --alert         # + page operator on wedge / repeat / deadman
    lane_wedge_watchdog.py --once --json   # single scan, machine-readable
    lane_wedge_watchdog.py --loop          # self-cadenced loop (default 60s)
    lane_wedge_watchdog.py --arm=nudge     # ARM auto-nudge (lease-gated). Reviewer only.
    lane_wedge_watchdog.py --arm           # ARM full ladder (nudge -> escalate). Reviewer only.
    lane_wedge_watchdog.py --self-test     # offline state-machine checks (no DB, no tmux)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ORCH_DIR = Path(__file__).resolve().parent.parent
# Self-contained import: under launchd there is NO PYTHONPATH, and the context
# watchdog hit 94% unwarned on 2026-07-21 because `import nervous_system`
# ModuleNotFound'd and its alert path crashed silently. Never depend on the env
# for our own imports.
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_ORCH_DIR / ".env")

# CAI-RESP-501: recovery ACTIONS are the watchdog pen (iii) ACTING — gated on the
# fleet_health_lease single-owner lease (default holder cc-fleet-health; hub
# reclaims on expiry) so the SRE and a reclaiming hub never both act on an agent.
from scripts.lib import fleet_health_lease  # noqa: E402
from scripts.lib import fire_window  # noqa: E402  (quiesce during a recycle fire window)
from scripts.lib import pane_busy  # noqa: E402  (footer-scoped busy check)
# CAI-786 wake predicate — the ONE source of "would this row wake the recipient",
# reused here so the hub's page gate applies the exact CAI-451 narrow floor.
from nervous_system import agent_wake  # noqa: E402

STATE_FILE = _ORCH_DIR / "logs" / "lane_wedge_watchdog_state.json"
LOG_FILE = _ORCH_DIR / "logs" / "lane_wedge_watchdog.log"
HEARTBEAT_FILE = _ORCH_DIR / "logs" / "lane_wedge_watchdog_heartbeat"
# Per-lane page SNOOZE: {lane: until_iso}. A lane here is still detected + LOGGED (durable record),
# but its PAGE is suppressed until `until_iso` — for a KNOWN-benign recurring ghost pending a recycle
# (Nazim #29644: stop re-P1-storming storefront's idle 'check-inbox' ghost). Fail-OPEN: any read/parse
# error ⇒ not snoozed ⇒ a real wedge still pages (a snooze must never silence a genuine stall).
SNOOZE_FILE = _ORCH_DIR / "logs" / "wedge_snooze.json"
_COMPOSER_LIB = _ORCH_DIR / "scripts" / "lib" / "composer_capture.sh"


# ---------------------------------------------------------------------------
# Config — all overridable via environment (launchd EnvironmentVariables).
# ---------------------------------------------------------------------------

def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Signal A thresholds. A wedge candidate needs an actionable message that has sat
# unread longer than MIN_AGE (the agent is not consuming its inbox) but not older
# than MAX_AGE (chronic un-mark_read backlog — the hub carries ~110 stale unread
# rows and lanes rarely mark_read; counting those would flag every idle body
# forever, the exact spam priority_sla_watchdog documents). QUIET_SEC: the agent
# has not WRITTEN to the bus within this window (a live-working agent emits rows;
# a wedged one is silent) — this is what separates "wedged" from "busy elsewhere".
UNREAD_MIN_AGE_SEC = _envint("LANE_WEDGE_UNREAD_MIN_AGE_SEC", 20 * 60)   # 20 min
UNREAD_MAX_AGE_SEC = _envint("LANE_WEDGE_UNREAD_MAX_AGE_SEC", 6 * 3600)  # 6 h
QUIET_SEC = _envint("LANE_WEDGE_QUIET_SEC", 20 * 60)                     # 20 min

# A lane doing SHORT tasks with ~30-min idle gaps (cycling during an incident tail)
# is not stalled — it drains on each nudge and progresses (Nazim 14413). Only PAGE a
# wedge that is a GENUINE stall: either fully quiet past this floor, OR sitting on an
# ACTIONABLE (requires_response) unread — the 2026-07-29 money-grant class. Below the
# floor + no actionable unread => still nudge (cheap, helps it drain) but do NOT page
# and do NOT count toward the repeat-wedge breaker. Keeps the real-stall catch, drops
# the benign-idle chatter.
ALERT_QUIET_SEC = _envint("LANE_WEDGE_ALERT_QUIET_SEC", 90 * 60)         # 90 min

# A wedge is DECLARED only when the candidate signature is stable across this many
# consecutive scans AND this many seconds of wall-clock — two independent floors
# so neither a burst of fast scans nor one delayed scan fires it early. At a 60s
# cadence, 4 polls + 300s ~= 5 min of a stable wedge before any action. Primary
# false-positive guard: widen, never narrow.
WEDGE_MIN_POLLS = _envint("LANE_WEDGE_MIN_POLLS", 4)
WEDGE_GRACE_SEC = _envint("LANE_WEDGE_GRACE_SEC", 300)

# After an auto-nudge, wait this long (and require this many nudges) before
# escalating — give the agent time to actually pick up its inbox.
STAGE2_DELAY_SEC = _envint("LANE_WEDGE_STAGE2_DELAY_SEC", 180)
MIN_NUDGES_BEFORE_ESCALATE = _envint("LANE_WEDGE_MIN_NUDGES_BEFORE_ESCALATE", 1)

# Repeat-wedge circuit breaker: an agent that wedges this many times inside the
# window is a deeper problem than a dropped inbox — stop auto-acting and page.
REPEAT_K = _envint("LANE_WEDGE_REPEAT_K", 3)
REPEAT_WINDOW_SEC = _envint("LANE_WEDGE_REPEAT_WINDOW_SEC", 6 * 3600)

# Dead-man's-switch: a scan starting more than this long after the previous
# heartbeat means the watchdog was down/stalled — page once. 4x the 60s cadence
# tolerates a normal skipped run; a real outage is far larger.
DEADMAN_GAP_SEC = _envint("LANE_WEDGE_DEADMAN_GAP_SEC", 240)

# Prune per-agent episode state / wedge history not seen for this long.
STATE_TTL_SEC = _envint("LANE_WEDGE_STATE_TTL_SEC", 24 * 3600)

# Self-cadenced loop interval (only used with --loop; launchd drives --once).
LOOP_INTERVAL_SEC = _envint("LANE_WEDGE_LOOP_INTERVAL_SEC", 60)

# Sessions NOT to treat as generic recoverable lanes in the DYNAMIC enumeration.
# The console (nazim), the hub console (orch), infra, and any operator-managed
# lane. cai / cc-fleet-health are NOT excluded from monitoring — they are handled
# by the SINGLETON registry below with their own sanctioned recovery tools; this
# list only filters the tmux-lane discovery so they are not ALSO treated as
# generic lanes.
EXCLUDE_SESSIONS = {
    s.strip() for s in os.environ.get(
        "LANE_WEDGE_EXCLUDE",
        "nazim,orch,orchestrator,infra").split(",") if s.strip()
}

# Which singleton bodies to monitor via Signal A (bus-only). Configurable so the
# operator can narrow it. Each maps to a recovery spec in _SINGLETONS below.
MONITOR_SINGLETONS = [
    s.strip() for s in os.environ.get(
        "LANE_WEDGE_SINGLETONS",
        "cai,cc-orchestrator,cc-fleet-health").split(",") if s.strip()
]

# Hub reachability for its (best-effort) auto-nudge — the hub is remote; PREFER
# Signal A for detection (no SSH), and only touch it over SSH when armed. Session
# + ssh host mirror priority_sla_watchdog's HUB conventions.
HUB_SSH = os.environ.get("LANE_WEDGE_HUB_SSH", "Musa@mac-studio")
HUB_TMUX_SESSION = os.environ.get("LANE_WEDGE_HUB_SESSION", "orch")
REMOTE_TMUX = os.environ.get("LANE_WEDGE_REMOTE_TMUX", "/opt/homebrew/bin/tmux")


def _tmux_bin() -> str:
    """Mini lanes live on the /usr/local/bin/tmux server (two-tmux note). Resolve
    it the way reset_lane.sh does; never a bare `tmux`."""
    override = os.environ.get("LANE_WEDGE_TMUX")
    if override:
        return override
    if os.path.exists("/usr/local/bin/tmux"):
        return "/usr/local/bin/tmux"
    return shutil.which("tmux") or "/usr/local/bin/tmux"


TM = _tmux_bin()


# Singleton bodies: bus identity, tmux session (if locally readable), the recovery
# 'kind', and how to nudge. kind='singleton' => escalate = PAGE, never auto-reset.
# composer='delegated' => we do NOT read their composer in the watchdog; the
# sanctioned nudge tool (nudge_cai.sh / the hub tmux guard) applies the final
# ghost/real safety check itself, so detection is Signal-A-only and host-agnostic.
_SINGLETONS = {
    "cai": {"kind": "singleton", "nudge": "nudge_cai",
            "label": "cai (governance node)"},
    "cc-orchestrator": {"kind": "singleton", "nudge": "nudge_hub",
                        "label": "the hub (cc-orchestrator)"},
    "cc-fleet-health": {"kind": "singleton", "nudge": "none",
                        "label": "cc-fleet-health (the SRE itself)"},
}

# A singleton whose tmux pane is LOCALLY readable gets the same 'working'
# (esc-to-interrupt) suppression as lanes, so a long inference is not misread as a
# wedge (Nazim 14067/14103). Only read when it's already a candidate + the session
# exists locally. Omitted => Signal-A-only: the hub 'orch' pane lives on the Studio,
# and reading it every scan over SSH is too heavy for the scan path.
SINGLETON_SESSIONS = {
    kv.split(":")[0].strip(): kv.split(":")[1].strip()
    for kv in os.environ.get("LANE_WEDGE_SINGLETON_SESSIONS",
                             "cai:cai,cc-fleet-health:fleet-health").split(",")
    if ":" in kv
}

# This body's own bus identity — its idle is suppressed while its lease is fresh.
SELF_AGENT = "cc-fleet-health"


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


def _dsn() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _pg_connect():
    try:
        import psycopg  # type: ignore
        return psycopg.connect
    except ImportError:  # pragma: no cover
        try:
            import psycopg2  # type: ignore
            return psycopg2.connect
        except ImportError:
            return None


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception:
        s = {}
    s.setdefault("agents", {})
    s.setdefault("wedge_history", {})
    s.setdefault("deadman", {})
    return s


def save_state(state: dict) -> None:
    now = time.time()
    state["agents"] = {
        k: v for k, v in state.get("agents", {}).items()
        if now - v.get("last_seen", now) < STATE_TTL_SEC
    }
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
# Signals
# ---------------------------------------------------------------------------

@dataclass
class BusSignal:
    unread: int              # unanswered unread in the [MIN_AGE, MAX_AGE] window
    oldest_unread_age: float # seconds since the oldest such row (0 if none)
    last_write_age: float    # seconds since the agent last wrote a bus row (inf = never)
    actionable: int = 0      # of `unread`, how many require a response (req=True) —
                             # a stalling req=True row is a REAL stall (the 2026-07-29
                             # money-grant class), vs a req=False FYI that can idle.
    wake_eligible: int = 0   # of `unread`, how many would WAKE this recipient per the
                             # CAI-786 predicate (agent_wake.should_auto_wake). For the
                             # hub that is the CAI-451 narrow floor (P0/P1 + rr); used to
                             # gate the hub's page so benign P2/FYI unread never false-
                             # pages a legitimately-idle, cross-host, attended hub.

    @property
    def piling(self) -> bool:
        """Fresh-but-not-ancient unread work has been waiting past MIN_AGE."""
        return self.unread > 0 and self.oldest_unread_age >= UNREAD_MIN_AGE_SEC

    @property
    def quiet(self) -> bool:
        """The agent has not written to the bus within QUIET_SEC."""
        return self.last_write_age >= QUIET_SEC


# Composer states. 'empty' == empty OR a dim-ghost (safe to nudge — the buffer is
# genuinely empty). 'real' == real non-dim typed text (the agent's OWN draft —
# never auto-nudge). 'unreadable' == capture had no prompt row (do NOT assert
# empty). 'delegated' == not read here; the sanctioned nudge tool guards it.
# 'menu' == the pane is TRAPPED in an interactive selection menu (AskUserQuestion:
# footer 'up/down to navigate' / 'Esc to cancel' / 'Enter to select'). For an
# autonomous agent NObody is at the keyboard to answer, so the menu blocks the
# turn AND the bus indefinitely — genuinely STUCK, not idle and not working. This
# is the ~1-day cc-ihsanos trap the watchdog missed (Nazim 14937/14938). Alert-only
# (a plain nudge lands IN the menu — it needs an Esc first, staged separately).
COMP_EMPTY = "empty"
COMP_REAL = "real"
COMP_UNREADABLE = "unreadable"
COMP_DELEGATED = "delegated"
COMP_MENU = "menu"


@dataclass
class ComposerSignal:
    state: str
    text: str = ""
    working: bool = False   # pane footer shows a LIVE turn ('esc to interrupt') —
                            # the agent is mid-inference, not idle-at-prompt. A long
                            # high-effort turn writes nothing to the bus while it
                            # thinks, so Signal A (bus-quiet) alone misreads it as a
                            # wedge. This is the SAME check lane_nudge.pane_working()
                            # uses. Suppresses the wedge (Nazim 14067/14103/14470).
    capped: bool = False    # pane shows the CC weekly-limit banner ('hit your weekly
                            # limit, resets ...') — the lane is POOL-EXHAUSTED and
                            # correctly WAITING for its weekly reset, NOT wedged. Idle
                            # + bus-silent + not-draining looks identical to a wedge
                            # but has a known benign cause; a reset/nudge can't help
                            # (op#14199 pool-crunch, Nazim #25930). Suppresses the
                            # wedge page + nudge; self-clears when the banner is gone.

    @property
    def safe_to_nudge(self) -> bool:
        """Auto-nudge is safe unless we POSITIVELY read the agent's own draft, or the
        pane is trapped in a menu. A count-only nudge appends to the buffer, so real
        text would be clobbered (07-04 phantom-injection class); and a nudge typed
        into an open menu just moves the selection (Nazim 13730) — both ALERT instead.
        empty / dim-ghost / unreadable / delegated defer to the nudge tool's guard."""
        return self.state not in (COMP_REAL, COMP_MENU)


@dataclass
class AgentObs:
    agent: str                 # bus identity (to_agent / from_agent)
    kind: str                  # 'lane' | 'singleton'
    session: Optional[str]     # tmux session (None for substrate-only bodies)
    bus: BusSignal
    composer: ComposerSignal
    reachable: bool = True     # tmux pane reachable (lanes only; True for bus-only)
    reason: str = ""


def read_bus_signal(agent: str, conn) -> BusSignal:
    """Signal A: unread pileup + quiet, straight off the substrate. Host-agnostic
    — works for the VPS hub with no SSH. 'Actionable' unread = created in the
    [MIN_AGE, MAX_AGE] window (fresh stall, not chronic backlog), excluding test
    rows AND rows the recipient has already ANSWERED. last_write_age spans all of
    the agent's rows.

    exclude-answered: a row the recipient has resolved is NOT an unhandled pileup,
    so it must not read as a wedge. Resolved = `responded_at` stamped, OR the agent
    wrote ANY later bus message — because bodies act on a message by REPLYING with a
    new one, not by stamping `responded_at` (#12486), so handled rows otherwise sit
    read_at NULL forever and inflate the count into a phantom-backlog false-positive
    (this watchdog's own cai/cc-irsyad flags were partly that). Only rows that
    arrived AFTER the agent's last activity and were never responded-to count."""
    with conn.cursor() as cur:
        # Fetch the windowed unread rows (not just counts) so wake-eligibility is decided
        # by the ONE shared predicate agent_wake.should_auto_wake — never forked into SQL.
        cur.execute(
            "SELECT am.priority, am.requires_response, am.message_type, "
            "       EXTRACT(epoch FROM now() - am.created_at) "
            "  FROM agent_messages am "
            " WHERE am.to_agent = %s AND am.read_at IS NULL AND am.is_test IS NOT TRUE "
            "   AND am.created_at <= now() - (%s * interval '1 second') "
            "   AND am.created_at >= now() - (%s * interval '1 second') "
            "   AND am.responded_at IS NULL "
            "   AND NOT EXISTS (SELECT 1 FROM agent_messages r "
            "                    WHERE r.from_agent = %s AND r.created_at > am.created_at)",
            (agent, UNREAD_MIN_AGE_SEC, UNREAD_MAX_AGE_SEC, agent))
        rows = cur.fetchall()
        unread = len(rows)
        oldest = max((float(r[3]) for r in rows if r[3] is not None), default=0.0)
        actionable = sum(1 for r in rows if r[1])
        wake_eligible = sum(
            1 for p, rr, mt, _ in rows
            if agent_wake.should_auto_wake(agent, mt or "", bool(rr), p or "P2", False))
        cur.execute(
            "SELECT EXTRACT(epoch FROM now() - max(created_at)) "
            "  FROM agent_messages WHERE from_agent = %s",
            (agent,))
        row = cur.fetchone()
        last_write = float(row[0]) if row and row[0] is not None else float("inf")
    return BusSignal(int(unread or 0), float(oldest or 0), last_write,
                     int(actionable or 0), int(wake_eligible or 0))


def read_composer(session: str) -> ComposerSignal:
    """Signal B: classify the composer via scripts/lib/composer_capture.sh (the
    fleet's ONE dim-ghost-vs-real definition — reused verbatim over shell-out, not
    reimplemented). CC_EMPTY=1 covers empty AND dim-ghost; CC_PARTIAL=noprompt
    means we could NOT read it (never assert empty); CC_N>0 with CC_EMPTY=0 is
    real staged text. CC_BUSY=1 (pane_busy: footer shows 'esc to interrupt', or a
    LIVE 'waiting for background agents' render) => the agent is mid-turn => working
    (never a wedge). pane_busy is called alongside the parse — it fails safe: any
    read miss leaves working=False, so we only ever SUPPRESS on a positive live
    read, never hide a real wedge."""
    # menu-trap: an interactive selection menu (AskUserQuestion) shows a NAV footer
    # ('up/down to navigate' / 'Esc to cancel' / 'Enter to select') — distinct from
    # the idle footer ('for agents') and the working footer ('esc to interrupt').
    # For an autonomous agent that footer == STUCK. Grep the last 6 rendered lines.
    snippet = (
        '. "$1" || exit 9; composer_parse_pane "$2" "$3" >/dev/null 2>&1; '
        'pane_busy "$2" "$3" >/dev/null 2>&1; '
        'menu=0; _pane="$("$2" capture-pane -t "$3" -p 2>/dev/null)"; '
        'printf "%s" "$_pane" | tail -6 | '
        'LC_ALL=C grep -qiE "to navigate|esc to cancel|enter to select" && menu=1; '
        'cap=0; printf "%s" "$_pane" | '
        'LC_ALL=C grep -qiE "hit your (weekly|usage|5-hour) limit|weekly limit.*reset" && cap=1; '
        'printf "RESULT %s %s %s %s %s %s\\n" "${CC_EMPTY:-x}" "${CC_N:-x}" "${CC_PARTIAL:-x}" "${CC_BUSY:-0}" "$menu" "$cap"; '
        'printf "FLAT %s\\n" "${CC_FLAT:-}"'
    )
    try:
        r = subprocess.run(["bash", "-c", snippet, "_", str(_COMPOSER_LIB), TM, session],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return ComposerSignal(COMP_UNREADABLE)
    empty = n = partial = "x"
    busy = menu = cap = "0"
    flat = ""
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("RESULT "):
            parts = ln.split()
            if len(parts) >= 7:
                empty, n, partial, busy, menu, cap = parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
            elif len(parts) >= 6:  # tolerate an older RESULT shape (no cap field)
                empty, n, partial, busy, menu = parts[1], parts[2], parts[3], parts[4], parts[5]
        elif ln.startswith("FLAT "):
            flat = ln[5:]
    working = busy == "1"
    # A CAPPED lane (pane shows the weekly-limit banner) is pool-exhausted + correctly
    # WAITING for its reset — NOT wedged. It reads idle+silent+not-draining (identical
    # to a wedge) but has a known benign cause a reset/nudge cannot fix. Classify it
    # capped so the decision suppresses the page+nudge; self-clears when the banner is
    # gone. Checked before menu/empty/real — it dominates (Nazim #25930).
    if cap == "1":
        return ComposerSignal(COMP_EMPTY, working=False, capped=True)
    # A menu-trap wins over every other classification: it is a live, non-empty pane
    # (would otherwise read as REAL/working), but it is STUCK awaiting input nobody
    # will give. Not 'working' — it is not making progress.
    if menu == "1":
        return ComposerSignal(COMP_MENU, working=False)
    if partial == "noprompt":
        return ComposerSignal(COMP_UNREADABLE, working=working)
    if empty == "1":
        return ComposerSignal(COMP_EMPTY, working=working)
    try:
        if int(n) > 0:
            return ComposerSignal(COMP_REAL, flat, working=working)
    except (TypeError, ValueError):
        pass
    return ComposerSignal(COMP_EMPTY, working=working)


def _pane_working(session: str) -> bool:
    """True iff the pane shows a live turn — the fleet's ONE definition, via
    read_composer's CC_BUSY. Fail-safe: a read miss returns False (no suppression)."""
    try:
        return read_composer(session).working
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pure wedge state machine — unit-tested offline. No DB, no tmux, no clock beyond
# the injected `now`.
# ---------------------------------------------------------------------------

V_HEALTHY = "healthy"            # no unread piling / agent writing -> nothing owed
V_UNREACHABLE = "unreachable"
V_MONITORING = "monitoring"      # wedge candidate, not yet past the stability floor
V_WEDGE = "wedge"                # confirmed wedge, composer safe -> nudge-eligible
V_WEDGE_UNSAFE = "wedge-unsafe"  # confirmed wedge but REAL staged draft -> alert only


_EPISODE_KEYS = ("sig", "first_seen", "poll_count", "nudged_at", "nudge_count",
                 "escalated_at", "alerted", "wedge_logged")


def _candidate(obs: AgentObs) -> bool:
    """Signal A holds: fresh unread piling AND the agent has gone quiet — AND the
    pane is not in a live turn. A long high-effort inference emits nothing to the
    bus while it thinks, so bus-quiet alone misreads it as wedged; a pane showing
    'esc to interrupt' is WORKING, not stalled (Nazim 14067/14103/14470). Suppress
    only on a POSITIVE live read (working defaults False) so a real wedge is never
    hidden by an unreadable pane."""
    return obs.bus.piling and obs.bus.quiet and not obs.composer.working


def _genuine_stall(obs: AgentObs) -> bool:
    """A confirmed wedge worth PAGING (and counting toward the repeat breaker).

    The HUB (cc-orchestrator) is a special case: it is CROSS-HOST (no composer read
    here), composer='delegated', and operator-attended, so 'fully quiet' cannot tell
    an idle-attended-done hub from a stuck one (Nazim #17117; the 315.7k-token lesson).
    Quiet-alone therefore FALSE-paged a legitimately-idle hub sitting on benign P2/FYI
    unread (#17116). So the hub pages ONLY on WAKE-ELIGIBLE unread it is failing to
    drain — the CAI-451 narrow floor (P0/P1 + requires_response, via should_auto_wake),
    the genuine actionable class — never on quiet + FYIs.

    Every other body is unchanged: either fully quiet past ALERT_QUIET_SEC, or holds an
    ACTIONABLE (requires_response) unread. A body that wrote more recently and has only
    FYI-grade unread is benign idle-between-tasks — nudged, never paged (Nazim 14413)."""
    if obs.agent == "cc-orchestrator":
        return obs.bus.wake_eligible > 0
    long_quiet = obs.bus.last_write_age >= ALERT_QUIET_SEC
    # NON-DING exclusion (console 34175): a LANE that only reads 'stalled' because it is
    # long-quiet — with a SAFE (empty/dim-ghost) composer and NO ding/actionable unread —
    # is benign expected-unread, NOT a wedge. The class: a NON-DING FYI pointer to an idle
    # auditor that reads-but-rarely-WRITES the bus (cc-quality #34144 -> the 34170 false
    # alert). Drop the quiet-alone stall THERE only. UNCHANGED and still stall: singletons
    # (the cai-incident quiet-alone case), a lane holding a REAL staged draft / menu (stuck
    # mid-task -> not safe_to_nudge), and any wake-eligible OR requires_response unread (the
    # genuinely-ignored actionable class — DING). Matches Nazim's boundary: keep (a) an
    # ignored DING/requires_response unread and (b) stuck-mid-task; drop only expected-unread.
    # (#34261 refinement) precondition is state != COMP_MENU, NOT safe_to_nudge: a passive
    # composer=real is UNRELIABLE for a non-ding idle lane (an idle-status line 'Idle --
    # inbox clear' reads as real, cc-quality #34258) — so it must not defeat this benign
    # classification and fire a passive stuck-mid-task P1. Such a lane is ACTIVE-PROBED
    # (lane_nudge) at the unsafe branch instead. Only a definitive MENU-trap (which cannot
    # be nudged — a plain nudge types into the menu) stays a genuine stall here.
    if (obs.kind == "lane" and obs.composer.state != COMP_MENU
            and obs.bus.wake_eligible == 0 and obs.bus.actionable == 0):
        long_quiet = False
    return long_quiet or obs.bus.actionable > 0


def evaluate(entry: Optional[dict], obs: AgentObs, now: float) -> "tuple[str, dict]":
    """Fold a fresh observation into this agent's episode state. Returns
    (verdict, new_entry). Pure — the runner decides what to DO with a wedge.

    Episode identity is a coarse signature of the wedge condition (unread bucket +
    composer state). If the agent DRAINS (unread drops), WRITES (goes non-quiet),
    or its composer changes class, that ends the episode — so a body making
    progress can never accumulate toward a wedge. Stage flags live only WITHIN one
    stable episode."""
    entry = dict(entry or {})
    entry["last_seen"] = now

    if not obs.reachable:
        for k in _EPISODE_KEYS:
            entry.pop(k, None)
        return V_UNREACHABLE, entry

    if not _candidate(obs):
        # empty inbox, or the agent is writing/consuming -> not wedged. End episode.
        for k in _EPISODE_KEYS:
            entry.pop(k, None)
        return V_HEALTHY, entry

    # Candidate. Same episode iff the wedge signature is unchanged.
    sig = f"{obs.bus.unread}|{obs.composer.state}"
    if entry.get("sig") == sig:
        entry["poll_count"] = int(entry.get("poll_count", 0)) + 1
    else:
        entry["sig"] = sig
        entry["first_seen"] = now
        entry["poll_count"] = 1
        for k in ("nudged_at", "nudge_count", "escalated_at", "alerted", "wedge_logged"):
            entry.pop(k, None)

    elapsed = now - float(entry.get("first_seen", now))
    stable = entry["poll_count"] >= WEDGE_MIN_POLLS and elapsed >= WEDGE_GRACE_SEC
    if not stable:
        return V_MONITORING, entry
    return (V_WEDGE if obs.composer.safe_to_nudge else V_WEDGE_UNSAFE), entry


# ---------------------------------------------------------------------------
# Recovery ACTIONS — reached only when armed AND the lease is held. Thin, audited
# wrappers over the sanctioned, self-guarding fleet tools. Module-level so tests
# can monkeypatch + assert they did / did NOT fire.
# ---------------------------------------------------------------------------

def _count_line(agent: str, n: int) -> str:
    return (f"\U0001F4E5 {n} bus message(s) addressed to you are unread past SLA — "
            f"read your agent_messages inbox and action/route each (mark read).")


def nudge_cai() -> "tuple[bool, str]":
    """cai: the sanctioned count-only injection (nudge_cai.sh) — it resolves cai's
    host itself and REFUSES on real staged text / a backed-up queue (its own ghost
    + queue guards). We do not pass free text (R1)."""
    try:
        r = subprocess.run([str(_ORCH_DIR / "scripts" / "nudge_cai.sh")],
                           capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).lower()
        ok = r.returncode == 0 and "no live cai session" not in out
        return ok, f"nudge_cai.sh rc={r.returncode}"
    except Exception as e:
        return False, f"nudge_cai.sh errored: {e}"


def nudge_hub(n: int) -> "tuple[bool, str]":
    """Hub: best-effort count-only tmux send over SSH (the hub is remote; PREFER
    Signal A for detection). Only fires if the hub pane is idle, and clears any
    stuck draft first. Mirrors priority_sla_watchdog's studio-tmux nudge."""
    line = _count_line("cc-orchestrator", n)
    sess = HUB_TMUX_SESSION

    def tm(*args: str) -> subprocess.CompletedProcess:
        remote = " ".join([REMOTE_TMUX] + ["'" + a.replace("'", "'\\''") + "'" for a in args])
        return subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", HUB_SSH, remote],
            capture_output=True, text=True, timeout=25)
    try:
        if tm("has-session", "-t", f"={sess}").returncode != 0:
            return False, f"no hub session '{sess}' on {HUB_SSH}"
        cap = tm("capture-pane", "-t", f"={sess}:0.0", "-p").stdout
        # LIVE FOOTER only (2026-08-16): the whole-pane grep made a hub with an old busy footer
        # in its scrollback permanently un-nudgeable. Unreadable => not busy (fail toward
        # delivery); the wedge alert this carries is the safety signal.
        if pane_busy.is_busy_text(cap, on_unreadable=False):
            return False, "hub busy (live footer shows a turn in progress) — not disturbing"
        if fire_window.is_held(sess):
            return False, "recycle fire window held — not typing mid-clear"
        tm("send-keys", "-t", f"={sess}:0.0", "C-u")
        time.sleep(0.3)
        tm("send-keys", "-t", f"={sess}:0.0", "-l", line)
        time.sleep(0.4)
        tm("send-keys", "-t", f"={sess}:0.0", "Enter")
        return True, f"studio-tmux:{sess}"
    except Exception as e:
        return False, f"hub nudge errored: {e}"


def nudge_lane(session: str, n: int) -> "tuple[bool, str]":
    """Lane: scripts/lane_nudge.sh — VERIFIED submit of a count-only line. It has
    its OWN SGR-aware ghost guard (2026-07-29) and REFUSES to clobber a lane's real
    staged draft, so it is the safe recovery even if our Signal B misread."""
    line = _count_line(session, n)
    try:
        r = subprocess.run([str(_ORCH_DIR / "scripts" / "lane_nudge.sh"), session, line],
                           capture_output=True, text=True, timeout=90)
        return r.returncode == 0, f"lane_nudge.sh rc={r.returncode}"
    except Exception as e:
        return False, f"lane_nudge.sh errored: {e}"


def do_nudge(obs: AgentObs) -> "tuple[bool, str]":
    """Route a count-only nudge by kind. Idempotence + the lease gate are the
    CALLER's (run) responsibility."""
    if obs.kind == "singleton":
        spec = _SINGLETONS.get(obs.agent, {})
        which = spec.get("nudge")
        if which == "nudge_cai":
            return nudge_cai()
        if which == "nudge_hub":
            return nudge_hub(obs.bus.unread)
        return False, "no auto-nudge for this singleton (escalate = page)"
    if obs.session:
        return nudge_lane(obs.session, obs.bus.unread)
    return False, "no session to nudge"


_RESET_REANCHOR = (
    "You were WEDGED — idle, holding your lease, with unread bus work you stopped "
    "draining. Reconcile your agent_messages inbox in full and resume your task.")


def _worktree_clean(session: str, lane_dirs: dict) -> "tuple[Optional[bool], str]":
    """Is the lane's git worktree clean? None = INDETERMINATE (no dir / not a repo
    / git failed) -> caller MUST treat as 'do not reset' (never /clear over work we
    cannot prove is committed)."""
    d = lane_dirs.get(session)
    if not d:
        r = _tmux("display-message", "-p", "-t", session, "#{pane_current_path}")
        if r and r.returncode == 0 and r.stdout.strip():
            d = r.stdout.strip()
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
            return False, f"{len(dirty.splitlines())} uncommitted change(s) in {root}"
        return True, f"clean ({root})"
    except Exception as e:
        return None, f"git check errored: {e}"


def reset_lane(session: str) -> "tuple[bool, str]":
    """Stage-2 for a LANE: scripts/reset_lane.sh. It refuses a BUSY lane and
    preserves the composer to a log; the git-clean guard is the caller's."""
    try:
        r = subprocess.run(
            [str(_ORCH_DIR / "scripts" / "reset_lane.sh"), session, _RESET_REANCHOR],
            capture_output=True, text=True, timeout=120)
        tail = (r.stdout or r.stderr).strip().splitlines()
        return r.returncode == 0, (tail[-1] if tail else f"rc={r.returncode}")
    except Exception as e:
        return False, f"reset_lane.sh errored: {e}"


# ---------------------------------------------------------------------------
# Operator page (nazim-console) — the safety-alert path. Ungated by the lease.
# ---------------------------------------------------------------------------

def _alert_subject(text: str) -> str:
    """Concise bus subject from the alert body's first meaningful line (its title)."""
    for raw in text.splitlines():
        line = raw.strip().lstrip("⚠🚨🐛️ ").strip()
        if line:
            return ("[wedge-watchdog] " + line)[:180]
    return "[wedge-watchdog] fleet wedge alert"


def _emit_bus_alert(text: str) -> None:
    """Surface an alert as an ATTRIBUTABLE bus row from cc-fleet-health to
    orch-console — the SRE's sanctioned outbound (charter §5); Nazim's console
    voices it to the operator. This is deliberately NOT nazim_send.sh: that is
    Nazim's OWN Telegram voice, and it fails silently off the hub host, which is
    why detect-only alerts only ever reached a log file (op#8807 stage-2). A bus
    row is durable, attributable, and host-agnostic (works from the Mini).

    requires_response=false + responded_at stamped so an awareness alert never
    re-creates the SLA-watchdog false-stall flood (#12486/#13168). Raises on
    failure so the caller can be LOUD — a page that fails silently is worse than
    none (dead-man's-switch)."""
    connect = _pg_connect()
    dsn = _dsn()
    if connect is None or not dsn:
        raise RuntimeError("no DB driver / DSN — cannot emit bus alert")
    conn = connect(dsn, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute(
                "INSERT INTO agent_messages "
                "(from_agent, to_agent, message_type, subject, body, priority, "
                " requires_response, responded_at) "
                "VALUES ('cc-fleet-health', 'orch-console', 'update', %s, %s, 'P1', "
                " false, now())",
                (_alert_subject(text), text))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _lane_snoozed(session: str) -> bool:
    """True iff `session` has an unexpired page-snooze in SNOOZE_FILE. Fail-OPEN: any
    missing/unparseable file or bad timestamp returns False, so a real wedge always pages
    (a snooze must never silence a genuine stall — dead-man's-switch). Detection + logging
    are unaffected; only the operator PAGE is suppressed while snoozed."""
    try:
        data = json.loads(SNOOZE_FILE.read_text())
        until = data.get(session)
        if not until:
            return False
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until_dt
    except Exception:
        return False


def _page(text: str) -> None:
    """Surface an operator alert. In tests / manual inspection
    (LANE_WEDGE_ALERT_STDOUT=1 or pytest) print instead — never touch the bus.
    Otherwise emit an attributable bus row to orch-console (cc-fleet-health's
    sanctioned channel). A page must never crash the loop, but a FAILED page must
    be LOUD, never silent (dead-man's-switch)."""
    if os.environ.get("LANE_WEDGE_ALERT_STDOUT") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        print("[PAGE-would-send]\n" + text + "\n")
        return
    try:
        _emit_bus_alert(text)
        log(f"bus-alert -> orch-console: {_alert_subject(text)}")
    except Exception as e:  # a page must never crash the loop — but must be LOUD
        log(f"ALERT-DELIVERY-FAILED (bus emit) — operator NOT notified: {e}")
        print(f"[lane-wedge] ALERT-DELIVERY-FAILED: {e}", file=sys.stderr)


def _label(obs: AgentObs) -> str:
    if obs.kind == "singleton":
        return _SINGLETONS.get(obs.agent, {}).get("label", obs.agent)
    return f"lane '{obs.session or obs.agent}'"


def _wedge_alert(obs: AgentObs, elapsed_min: int, unsafe: bool, armed: bool) -> str:
    label = _label(obs)
    do = ("Its composer holds text read as REAL-per-content — but this is NOT probe-verified, "
          "so it may be a re-rendered / scrolled-off ghost, not a genuine draft. Run lane_nudge "
          "to disambiguate (it probes: clears+delivers if ghost, refuses if genuinely staged). "
          "If it IS its own draft, submit its step or clear the inbox." if unsafe else
          ("Auto-nudge fired — a SINGLE best-effort nudge, NOT a sustained recovery. "
           "CONFIRM it actually drained (a nudge can fail / be refused); if it did not, it "
           "needs a human — a repeat wedge re-pages, but nothing else auto-runs." if armed else
           "It self-heals once nudged to drain its inbox — nudge it or arm the watchdog."))
    try:
        from nervous_system.alert_format import format_alert
        return format_alert(
            icon="⚠️",
            title=f"{label} looks wedged — idle but not draining its inbox",
            what=(f"{label} has {obs.bus.unread} unread bus message(s) waiting ~{elapsed_min} min, "
                  f"has not written to the bus in {int(obs.bus.last_write_age)//60} min, and is idle."),
            why=("A wedged agent is alive and holds its lease, so every other gauge reads it green; "
                 "on 2026-07-29 this silently stalled a live money-path grant for ~6h."),
            do=do,
            detail=(f"agent={obs.agent} kind={obs.kind} composer={obs.composer.state} "
                    f"unread={obs.bus.unread} oldest={int(obs.bus.oldest_unread_age)//60}m "
                    f"quiet={int(obs.bus.last_write_age)//60}m; "
                    f"recovery {'ARMED' if armed else 'DETECT-ONLY'}."),
            ref="LANE-WEDGE-WATCHDOG",
        )
    except Exception:
        return (f"⚠️ {label} looks wedged — {obs.bus.unread} unread, idle ~{elapsed_min}m, "
                f"silent {int(obs.bus.last_write_age)//60}m. {do}")


def _menu_trap_alert(obs: AgentObs, elapsed_min: int) -> str:
    label = _label(obs)
    do = ("Open its pane and press Esc to dismiss the menu, then let it drain / re-answer — "
          "a plain nudge won't help (it types INTO the menu). Never auto-reset a singleton.")
    try:
        from nervous_system.alert_format import format_alert
        return format_alert(
            icon="🚨",
            title=f"{label} is TRAPPED in a menu — stuck, blocking its bus",
            what=(f"{label}'s pane is sitting in an interactive selection menu (AskUserQuestion — "
                  f"'up/down to navigate / Esc to cancel'), so its turn AND its bus are frozen: "
                  f"{obs.bus.unread} unread piling ~{elapsed_min} min, quiet "
                  f"{int(obs.bus.last_write_age)//60} min."),
            why=("An autonomous agent has nobody at the keyboard to answer the menu, so it stays "
                 "stuck indefinitely — a menu-trap silently cost cc-ihsanos ~1 DAY (14 unread incl "
                 "cai grants piled up) and read as neither idle nor working."),
            do=do,
            detail=(f"agent={obs.agent} kind={obs.kind} composer=menu unread={obs.bus.unread} "
                    f"oldest={int(obs.bus.oldest_unread_age)//60}m quiet={int(obs.bus.last_write_age)//60}m."),
            ref="LANE-WEDGE-WATCHDOG / MENU-TRAP",
        )
    except Exception:
        return (f"🚨 {label} is TRAPPED in a selection menu (AskUserQuestion) — stuck, "
                f"{obs.bus.unread} unread piling ~{elapsed_min}m. Press Esc on its pane + let it drain; "
                f"a plain nudge types into the menu.")


def _repeat_alert(obs: AgentObs, n: int) -> str:
    label = _label(obs)
    try:
        from nervous_system.alert_format import format_alert
        return format_alert(
            icon="🚨",
            title=f"{label} keeps re-wedging — auto-recovery stopped",
            what=f"{label} has wedged {n} times in the repeat window; the watchdog STOPPED auto-acting.",
            why="An agent that re-wedges after a nudge is a deeper problem than a stuck inbox.",
            do=f"Look at {label} by hand — its task, boot state, or the harness may be stuck.",
            detail=f"repeat_k={REPEAT_K} window={REPEAT_WINDOW_SEC//3600}h",
            ref="LANE-WEDGE-WATCHDOG / REPEAT-WEDGE",
        )
    except Exception:
        return f"🚨 {label} wedged {n}x in {REPEAT_WINDOW_SEC//3600}h — auto-recovery STOPPED. Check it."


def _escalate_singleton_page(obs: AgentObs) -> str:
    label = _label(obs)
    try:
        from nervous_system.alert_format import format_alert
        return format_alert(
            icon="🚨",
            title=f"{label} STILL wedged after a nudge — needs you",
            what=f"{label} was nudged to drain its inbox but is still idle with {obs.bus.unread} unread.",
            why=("It is a singleton body with no failover and is NEVER auto-reset — while it is wedged, "
                 "everything behind it is stalled."),
            do=f"Open {label} and drain/route its inbox, or reset it via its own reset path.",
            detail=f"agent={obs.agent} unread={obs.bus.unread}",
            ref="LANE-WEDGE-WATCHDOG / SINGLETON-ESCALATE",
        )
    except Exception:
        return (f"🚨 {label} still wedged after a nudge — {obs.bus.unread} unread. It is never "
                f"auto-reset; drain/route its inbox or reset it by hand.")


# ---------------------------------------------------------------------------
# Enumeration — the monitored set = singletons (bus-only) + live lanes (bus +
# local composer).
# ---------------------------------------------------------------------------

def lane_agent_map(conn) -> "tuple[dict, dict]":
    """(session -> base_agent_id, session -> worktree_path) from fleet_lanes."""
    a2b, dirs = {}, {}
    with conn.cursor() as cur:
        cur.execute("SELECT lane, base_agent_id, worktree_path FROM fleet_lanes")
        for lane, base, wt in cur.fetchall():
            if lane and base:
                a2b[lane] = base
            if lane and wt:
                dirs[lane] = wt
    return a2b, dirs


def list_lane_sessions() -> list[str]:
    r = _tmux("list-sessions", "-F", "#{session_name}")
    if r is None or r.returncode != 0:
        return []
    return [n.strip() for n in r.stdout.splitlines()
            if n.strip() and n.strip() not in EXCLUDE_SESSIONS
            and n.strip() != HUB_TMUX_SESSION]


def _has_local_session(session: str) -> bool:
    r = _tmux("has-session", "-t", "=" + session)
    return bool(r and r.returncode == 0)


def gather_observations(conn) -> list[AgentObs]:
    """Build the live observation set. Singletons: Signal A (+ a local 'working'
    read when the pane is here). Lanes: Signal A (via base_agent_id) + Signal B
    (local composer)."""
    obs: list[AgentObs] = []
    # Singletons — bus-only + a working-read when the pane is locally present.
    for agent in MONITOR_SINGLETONS:
        try:
            bus = read_bus_signal(agent, conn)
        except Exception as e:
            log(f"bus read failed for singleton {agent}: {e}")
            continue
        working = False
        # The SRE (this body) is nudge/schedule-driven: idle-between-wakes is its
        # NORMAL state, not a wedge. Its liveness is the lease heartbeat, not bus
        # activity — a FRESH self-lease ('holder-current') proves this body is alive
        # (renewal is tied to it). Suppress the self-wedge while fresh; a STALE lease
        # ('holder-stale-self' — heartbeat stopped) is a real problem, left to surface
        # (and the hub reclaims). This kills the recurring SRE-idle false page.
        if agent == SELF_AGENT:
            try:
                if fleet_health_lease.check()[1] == "holder-current":
                    working = True
            except Exception:
                pass
        # Suppress the live-inference false page: if it's already a candidate and its
        # pane is here, treat 'esc to interrupt' as working (not wedged). Composer
        # stays DELEGATED — the nudge tool keeps its own ghost guard.
        sess = SINGLETON_SESSIONS.get(agent)
        if not working and sess and bus.piling and bus.quiet and _has_local_session(sess):
            working = _pane_working(sess)
        obs.append(AgentObs(agent=agent, kind="singleton", session=None, bus=bus,
                            composer=ComposerSignal(COMP_DELEGATED, working=working)))
    # Lanes — enumerate the live tmux server, map to bus identity.
    try:
        a2b, _dirs = lane_agent_map(conn)
    except Exception as e:
        log(f"fleet_lanes map failed ({e}) — no dynamic lanes this scan")
        a2b = {}
    unmapped: list[str] = []
    for sess in list_lane_sessions():
        base = a2b.get(sess)
        if not base:
            # No fleet_lanes(session->base) row — we can't read Signal A for it, so it
            # is UNMONITORED. A stale/missing mapping is exactly how cc-ihsanos (session
            # 'mirror' in fleet_lanes, but live as 'ihsanos-platform') went unwatched for
            # ~1 day. Surface the blind spot so it gets reconciled (Nazim 14937/14938).
            unmapped.append(sess)
            continue
        if base in MONITOR_SINGLETONS:
            continue  # already covered by the singleton registry — never double-track
                      # the same bus identity (its episode state is keyed on `base`)
        try:
            bus = read_bus_signal(base, conn)
        except Exception as e:
            log(f"bus read failed for lane {sess}/{base}: {e}")
            continue
        # Only pay the composer read when Signal A already implicates the lane —
        # Signal B just confirms it is SAFE to nudge, so skip it otherwise.
        comp = read_composer(sess) if (bus.piling and bus.quiet) else ComposerSignal(COMP_EMPTY)
        obs.append(AgentObs(agent=base, kind="lane", session=sess, bus=bus, composer=comp))
    if unmapped:
        log(f"COVERAGE-GAP: {len(unmapped)} live tmux session(s) UNMAPPED in fleet_lanes "
            f"-> UNMONITORED (reconcile the mapping): {','.join(sorted(unmapped))}")
    return obs


# ---------------------------------------------------------------------------
# Dead-man's-switch
# ---------------------------------------------------------------------------

def _deadman_check_and_beat(state: dict, alert: bool) -> None:
    now = time.time()
    try:
        last = float(HEARTBEAT_FILE.read_text().strip())
    except Exception:
        last = float(state.get("deadman", {}).get("last_beat", 0) or 0)
    gap = now - last
    if last and gap > DEADMAN_GAP_SEC:
        msg = (f"🐛 Lane-wedge watchdog was DOWN for ~{int(gap)//60} min "
               f"(gap {int(gap)}s > {DEADMAN_GAP_SEC}s) and just resumed — wedged agents "
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
# Modes
# ---------------------------------------------------------------------------

MODE_DETECT = "detect-only"
MODE_NUDGE = "auto-nudge"
MODE_ESCALATE = "auto-escalate"
_MODE_RANK = {MODE_DETECT: 0, MODE_NUDGE: 1, MODE_ESCALATE: 2}


def resolve_mode(arm_arg: Optional[str]) -> str:
    """--arm flag wins; else env LANE_WEDGE_ARM; else detect-only. `--arm` with no
    value == escalate (full ladder); `--arm=nudge` stops at auto-nudge."""
    if arm_arg is not None:
        return {"nudge": MODE_NUDGE, "escalate": MODE_ESCALATE}.get(arm_arg, MODE_ESCALATE)
    env = (os.environ.get("LANE_WEDGE_ARM") or "").strip().lower()
    if env in ("nudge",):
        return MODE_NUDGE
    if env in ("escalate", "reset", "arm", "1", "true"):
        return MODE_ESCALATE
    return MODE_DETECT


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def _recover(obs: AgentObs, entry: dict, mode: str, alert: bool, now: float,
             lane_dirs: dict, line: dict) -> None:
    """The recovery ladder for a confirmed, SAFE wedge (V_WEDGE) — reached only
    when mode >= auto-nudge AND the lease is held (both checked by `run`). Mutates
    `entry` (episode stage flags) and `line` (report)."""
    # Stage 1 — auto-nudge, once per episode.
    if not entry.get("nudged_at"):
        ok, mech = do_nudge(obs)
        entry["nudged_at"] = now
        entry["nudge_count"] = int(entry.get("nudge_count", 0)) + 1
        line["action"] = f"auto-nudge {obs.agent}: {'ok' if ok else 'FAILED'} ({mech})"
        log(line["action"])
        # GAP-2 rc=255 BACKSTOP (Nazim 35141): a FAILED singleton nudge = unreachable = DEAD
        # evidence (this is exactly how cai's corpse got nudged for 30h). DEFER the page IFF the
        # singleton-liveness monitor covers this agent (it owns the single page); else (the
        # cross-host HUB, or any singleton with no local session) the backstop is the ONLY
        # detector on the nudge path -> it MUST page, never silently defer to a monitor that
        # never checks it. This DEAD-page is a safety detection alert, not a recovery action.
        if obs.kind == "singleton":
            try:
                from nervous_system import singleton_liveness as _sl
            except ImportError:
                import singleton_liveness as _sl
            # Cross-host bodies (the hub) are ALIVE per their orch_lease even when a Mini-side
            # nudge fails — so gate the uncovered DEAD-page on lease freshness (Nazim 37448): a
            # failed Mini→VPS nudge is unreachable-FROM-HERE, NOT death. Only the hub is
            # lease-tracked; every other agent passes lease_fresh=None (fail-safe → page).
            lease_fresh = _sl.hub_lease_fresh() if obs.agent == _sl.HUB_AGENT else None
            act = _sl.backstop_action(obs.agent, nudge_ok=ok, lease_fresh=lease_fresh)
            if act == "alive_unreachable":
                # lease FRESH: alive but un-nudgeable from the Mini. Surface the WEDGE (so a live
                # body unwedges it) + name the ssh remedy — never a false DEAD/needs-boot page.
                _sl.page_wedged_alive(obs.agent, dry_run=False)
                line["action"] += (" — UNCOVERED but orch_lease FRESH → WEDGED-alive "
                                   "(needs cross-host ssh nudge), NOT dead")
                log(f"BACKSTOP {obs.agent}: nudge failed + uncovered but orch_lease FRESH → "
                    f"paged WEDGED-alive (cross-host ssh nudge needed), NOT dead")
            elif act == "page":
                _sl.page_dead(obs.agent, dry_run=False)
                line["action"] += " — UNCOVERED singleton unreachable → paged DEAD (backstop)"
                log(f"BACKSTOP {obs.agent}: nudge failed + uncovered → paged DEAD (needs boot)")
            elif act == "defer":
                log(f"BACKSTOP {obs.agent}: nudge failed (likely dead) → deferring page to "
                    f"singleton-liveness monitor (covered)")
        return

    if mode != MODE_ESCALATE:
        line["action"] = f"nudged {int(now - entry['nudged_at'])}s ago — escalate disarmed (arm=nudge)"
        return

    # Stage 2 — escalate, only after the delay + a prior nudge.
    if now - float(entry["nudged_at"]) < STAGE2_DELAY_SEC:
        line["action"] = (f"nudged {int(now - entry['nudged_at'])}s ago — waiting "
                          f"{STAGE2_DELAY_SEC}s before escalating")
        return
    if int(entry.get("nudge_count", 0)) < MIN_NUDGES_BEFORE_ESCALATE:
        line["action"] = "escalate held — nudge count below floor"
        return
    if entry.get("escalated_at"):
        line["action"] = f"already escalated {int(now - entry['escalated_at'])}s ago — holding"
        return

    if obs.kind == "singleton":
        # NEVER auto-reset a singleton body — page the operator instead.
        if alert:
            _page(_escalate_singleton_page(obs))
        entry["escalated_at"] = now
        line["action"] = f"escalate {obs.agent}: singleton still wedged after nudge -> PAGED (never auto-reset)"
        log(line["action"])
        return

    # Lane escalation — guarded reset. NEVER reset over uncommitted work.
    clean, why = _worktree_clean(obs.session, lane_dirs)
    if clean is not True:
        if alert:
            _page(f"⚠️ Lane '{obs.session}' still wedged after a nudge, but its git worktree "
                  f"is not clean ({why}) — NOT resetting (would risk uncommitted work). "
                  f"Please look at it by hand.")
        entry["escalated_at"] = now
        line["action"] = f"escalate SKIPPED — worktree not clean ({why}); alerted instead"
        log(line["action"])
        return
    ok, detail = reset_lane(obs.session)
    entry["escalated_at"] = now
    line["action"] = f"escalate reset_lane '{obs.session}': {'ran' if ok else 'FAILED'} — {detail} [{why}]"
    log(line["action"])


def run(mode: str = MODE_DETECT, alert: bool = False, as_json: bool = False,
        injected: Optional[list[AgentObs]] = None, lane_dirs: Optional[dict] = None,
        persist: Optional[bool] = None) -> int:
    """One scan. `injected` (list[AgentObs]) + lane_dirs are the test seam. persist
    defaults to (mode != detect-only) so a manual detect run never mutates prod
    state with phantom episodes — but cross-scan episode tracking is needed even in
    detect-only for real classification, so dry persistence is opt-in via
    LANE_WEDGE_PERSIST_DRY=1 (the scheduled --alert run sets it)."""
    dry = mode == MODE_DETECT
    if persist is None:
        persist = (not dry) or os.environ.get("LANE_WEDGE_PERSIST_DRY") == "1"
    state = load_state()
    now = time.time()

    # Dead-man's-switch beats every scan (ungated — a safety signal is never gated).
    _deadman_check_and_beat(state, alert)

    # ACTIONS are lease-gated (CAI-RESP-501); detection + alerts are not. If a
    # different live body holds the pen, downgrade to detect-only (still classify +
    # log + alert) so the SRE and a reclaiming hub never both act on an agent.
    lease_why = "detect-only (no actions)"
    if not dry:
        lease_ok, lease_why = fleet_health_lease.gate()
        if not lease_ok:
            log(f"pen-gate: recovery DEFERRED (CAI-RESP-501) — {lease_why}; "
                f"downgrading to detect-only (detect+log+alert only)")
            dry = True
            persist = False

    if injected is not None:
        observations = injected
        lane_dirs = lane_dirs or {}
    else:
        conn = None
        try:
            connect = _pg_connect()
            dsn = _dsn()
            if connect is None or not dsn:
                log("no DB driver / DSN — cannot read Signal A this scan")
                observations = []
            else:
                conn = connect(dsn, connect_timeout=15)
                observations = gather_observations(conn)
                if lane_dirs is None:
                    _a2b, lane_dirs = lane_agent_map(conn)
        except Exception as e:
            log(f"db/gather failed: {e}")
            observations = []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        lane_dirs = lane_dirs or {}

    results: list[dict] = []
    for obs in observations:
        # GAP-2 LIVENESS PRECONDITION (Nazim 35141): a DEAD body is not wedged — never score or
        # nudge a corpse (the gap-2 root cause). Check liveness FIRST for a singleton; if DEAD,
        # skip wedge-scoring and DEFER the single page to the standalone singleton-liveness
        # monitor (bounded <=300s — both call the same classify_dead()). 'uncovered'/'alive'/
        # 'grace' -> proceed to normal wedge logic.
        if obs.kind == "singleton":
            try:
                from nervous_system import singleton_liveness as _sl
            except ImportError:  # run-as-script: nervous_system dir on sys.path
                import singleton_liveness as _sl
            if _sl.agent_liveness(obs.agent) == "dead":
                line = {"agent": obs.agent, "kind": obs.kind, "session": obs.session,
                        "verdict": "dead", "unread": obs.bus.unread,
                        "action": ("DEAD (no tmux) — NOT wedged; skipped scoring, deferring page "
                                   "to singleton-liveness monitor")}
                log(f"DEAD {obs.agent}: no tmux/process — not a wedge; deferring page to singleton-liveness")
                results.append(line)
                continue
        entry = state["agents"].get(obs.agent)
        verdict, entry = evaluate(entry, obs, now)
        state["agents"][obs.agent] = entry
        line = {"agent": obs.agent, "kind": obs.kind, "session": obs.session,
                "verdict": verdict, "unread": obs.bus.unread,
                "composer": obs.composer.state}

        if verdict in (V_HEALTHY, V_UNREACHABLE):
            results.append(line)
            continue

        elapsed_min = int((now - float(entry.get("first_seen", now))) / 60)
        line["polls"] = entry.get("poll_count")
        line["elapsed_min"] = elapsed_min

        # A CAPPED lane is pool-exhausted and correctly WAITING for its weekly reset —
        # idle+silent+not-draining looks identical to a wedge but is benign; a reset or
        # nudge cannot help until the pool resets. Suppress the page+nudge (log it, so a
        # suppressed alert is never silent), self-clears when the banner is gone (#25930).
        if obs.composer.capped:
            line["action"] = ("capped — weekly-limit banner; pool-exhausted, benignly WAITING "
                              "for reset (NOT a wedge; reset/nudge can't help)")
            log(f"CAPPED {obs.agent}: weekly-limit banner — wedge SUPPRESSED (benign, waiting reset)")
            results.append(line)
            continue

        if verdict == V_MONITORING:
            line["note"] = (f"candidate, monitoring ({entry['poll_count']}/{WEDGE_MIN_POLLS} polls, "
                            f"{elapsed_min}m/{WEDGE_GRACE_SEC//60}m)")
            results.append(line)
            continue

        # verdict is V_WEDGE (safe) or V_WEDGE_UNSAFE (real staged draft OR menu) ---
        unsafe = verdict == V_WEDGE_UNSAFE
        menu = obs.composer.state == COMP_MENU   # trapped in an AskUserQuestion menu
        history = state["wedge_history"].setdefault(obs.agent, [])
        recent = [t for t in history if now - t < REPEAT_WINDOW_SEC]

        # Repeat-wedge circuit breaker: stop auto-acting + page once — but only for a
        # GENUINE stall. A lane merely cycling between short tasks (wrote recently,
        # only FYI unread) must not trip the breaker (Nazim 14413/13969/14033); it
        # falls through to a normal (nudge-only, no-page) wedge below.
        if len(recent) >= REPEAT_K and _genuine_stall(obs):
            line["action"] = "REPEAT-WEDGE — auto-recovery STOPPED, paging operator"
            if not entry.get("repeat_alerted"):
                if alert and not _lane_snoozed(obs.session):
                    _page(_repeat_alert(obs, len(recent)))
                entry["repeat_alerted"] = now
            log(f"REPEAT-WEDGE {obs.agent}: {len(recent)} wedges in window — stop + page")
            results.append(line)
            continue

        # Log the wedge once per episode (durable record, any mode) — but only PAGE a
        # GENUINE stall (fully-quiet past ALERT_QUIET_SEC, or an actionable req=True
        # unread). A benign cycling wedge is still nudged below, just never paged, and
        # will page later if it does cross into a real stall (alerted set only on page).
        if not entry.get("wedge_logged"):
            entry["wedge_logged"] = now
            kind = "MENU-TRAP" if menu else ("WEDGE-UNSAFE" if unsafe else "WEDGE")
            log(f"{kind} {obs.agent}: {obs.bus.unread} unread "
                f"({obs.bus.actionable} actionable), idle {elapsed_min}m, "
                f"composer={obs.composer.state} ({'ARMED:'+mode if not dry else 'DETECT-ONLY'})")
        # A menu-trap ALWAYS surfaces (it is definitively stuck — blocks the bus).
        # Everything else — including an `unsafe` (real-per-content) composer — surfaces
        # ONLY on a genuine stall (actionable req=True unread, OR fully-quiet past
        # ALERT_QUIET_SEC). Arm-gating (Nazim #31259 / cc-fleet-health): `unsafe` no longer
        # alerts on its own — a lane idle on a single non-actionable unread whose composer
        # merely reads as real (often a re-rendered ghost, not a genuine draft) is the
        # cosmetic false-arm that pulled the operator in for nothing (ihsanos #31257).
        if (alert and (menu or _genuine_stall(obs)) and not entry.get("alerted")
                and not _lane_snoozed(obs.session)):
            _page(_menu_trap_alert(obs, elapsed_min) if menu
                  else _wedge_alert(obs, elapsed_min, unsafe, armed=not dry))
            entry["alerted"] = now

        if menu:
            # Trapped in a menu — a plain nudge lands IN the menu (needs Esc first,
            # staged separately). Alert only; never auto-nudge.
            line["action"] = "MENU-TRAP — pane stuck in a selection menu; alert-only (Esc+drain to recover)"
            results.append(line)
            continue

        if unsafe:
            # ACTIVE-PROBE (console #34261): a non-ding idle LANE's passive composer=real is
            # UNRELIABLE — an idle-status line ('Idle -- inbox clear') reads as real, yet
            # lane_nudge's ACTIVE probe confirms it a GHOST (cc-quality #34258/#34178, 3rd
            # false-P1). When armed+lease-held, probe ONCE via lane_nudge (it self-guards:
            # delivers if ghost/idle-status, REFUSES a genuine draft). DELIVER -> the lane
            # drains its benign FYI -> benign, no alert. REFUSE -> a genuine staged draft IS
            # a real stuck-mid-task -> alert-only; NEVER reset (a reset would clobber the very
            # draft this branch protects). Singletons / ding-unread / detect-only fall through
            # to the passive alert-only below (the passive 1276 alert was already gated off for
            # a non-ding lane by _genuine_stall).
            # Gate the probe on LONG-QUIET: a RECENTLY-active cycling lane (wrote within
            # ALERT_QUIET_SEC) self-drains its FYI on its next turn — waking it to probe is
            # the #31259 wasted-wake we already suppress. Only a LONG-QUIET non-ding lane
            # (won't self-drain, and whose composer=real is the suspicious cc-quality case)
            # is worth the active probe.
            nonding_lane = (obs.kind == "lane" and obs.bus.wake_eligible == 0
                            and obs.bus.actionable == 0
                            and obs.bus.last_write_age >= ALERT_QUIET_SEC)
            if nonding_lane and not dry:
                if not entry.get("nudged_at"):
                    ok, mech = do_nudge(obs)
                    entry["nudged_at"] = now
                    entry["nudge_count"] = int(entry.get("nudge_count", 0)) + 1
                    if ok:
                        line["action"] = (f"non-ding + passive-real -> ACTIVE-PROBED, lane_nudge "
                                          f"DELIVERED ({mech}) -> benign (lane drains, re-idles)")
                    else:
                        if alert and not entry.get("alerted") and not _lane_snoozed(obs.session):
                            _page(_wedge_alert(obs, elapsed_min, unsafe, armed=True))
                            entry["alerted"] = now
                        line["action"] = (f"non-ding + REAL draft confirmed by probe refusal ({mech}) "
                                          f"-> alert-only (draft protected, NOT reset)")
                    log(line["action"])
                else:
                    line["action"] = (f"non-ding + passive-real -> already active-probed "
                                      f"{int(now - float(entry['nudged_at']))}s ago (holding)")
                results.append(line)
                continue
            # REAL staged draft — NEVER auto-nudge (would clobber). Alert only.
            line["action"] = ("REAL-per-content (NOT probe-verified — may be a re-rendered/scrolled-off ghost) "
                               "— alert-only, never auto-nudged; run lane_nudge to disambiguate")
            results.append(line)
            continue

        # Arm-gating (Nazim #31259 / cc-fleet-health): a COSMETIC safe wedge — a candidate
        # (idle + unread piling) that is NOT a genuine stall (0 actionable AND wrote within
        # ALERT_QUIET_SEC) — is benign idle-between-tasks. Do NOT auto-nudge it: waking a
        # lane to drain a single non-actionable FYI is a wasted wake, not recovery. It self-
        # drains on its next turn, and if it ever crosses into a real stall the genuine-stall
        # gate above acts then. Logged already (durable record), just not acted on.
        if not _genuine_stall(obs):
            line["action"] = ("cosmetic wedge (candidate but not a genuine stall — 0 actionable, "
                              "wrote within ALERT_QUIET_SEC) — logged, not nudged")
            results.append(line)
            continue

        if dry:
            line["action"] = f"[DETECT-ONLY] WOULD auto-nudge {obs.agent} (unarmed)"
            results.append(line)
            continue

        # ARMED (mode >= auto-nudge) AND lease held -> recover. genuine_stall is now
        # guaranteed above, so this is always a real stall counting toward the repeat breaker.
        if not history or now - history[-1] > WEDGE_GRACE_SEC:
            history.append(now)
        _recover(obs, entry, mode, alert, now, lane_dirs, line)
        results.append(line)

    if persist:
        save_state(state)

    if as_json:
        print(json.dumps({"mode": mode, "alert": alert, "lease": lease_why,
                          "results": results}, indent=2))
    else:
        header = f"[lane-wedge] mode={mode} alert={'on' if alert else 'off'} " \
                 f"agents={len(results)} lease={lease_why}"
        print(header)
        for line in results:
            extra = line.get("note") or line.get("action") or ""
            print(f"  {line['verdict']:13} {str(line.get('agent')):18} "
                  f"unread={line.get('unread')} comp={line.get('composer')} {extra}")
    return 0


# ---------------------------------------------------------------------------
# Offline self-test — deterministic proof of the state machine, no DB / tmux.
# ---------------------------------------------------------------------------

def self_test() -> int:
    import tempfile
    global STATE_FILE, LOG_FILE, HEARTBEAT_FILE, WEDGE_MIN_POLLS, WEDGE_GRACE_SEC
    failures: list[str] = []
    _td = tempfile.mkdtemp(prefix="lane_wedge_selftest_")
    LOG_FILE = Path(_td) / "log"
    HEARTBEAT_FILE = Path(_td) / "hb"
    WEDGE_MIN_POLLS = 3
    WEDGE_GRACE_SEC = 120

    def check(cond, msg):
        if not cond:
            failures.append(msg)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    def mk(agent="cai", kind="singleton", session=None, unread=3, oldest=1800.0,
           last_write=1800.0, comp=COMP_DELEGATED, text=""):
        return AgentObs(agent, kind, session,
                        BusSignal(unread, oldest, last_write),
                        ComposerSignal(comp, text))

    t = 1000.0
    print("Healthy-idle (empty inbox) is NEVER a wedge")
    v, _ = evaluate(None, mk(unread=0, oldest=0, last_write=1e9), t)
    check(v == V_HEALTHY, "no unread -> healthy")
    print("Unread piling but agent still WRITING -> not wedged")
    v, _ = evaluate(None, mk(unread=5, oldest=3600, last_write=60), t)
    check(v == V_HEALTHY, "recent self-write -> healthy (busy, not wedged)")

    print("The cai case: unread piling + quiet + delegated composer -> WEDGE after floor")
    e = None
    obs = mk()  # cai, 3 unread @30m, silent 30m
    v, e = evaluate(e, obs, t);         check(v == V_MONITORING and e["poll_count"] == 1, "poll1 monitoring")
    v, e = evaluate(e, obs, t + 60);    check(v == V_MONITORING and e["poll_count"] == 2, "poll2 monitoring")
    v, e = evaluate(e, obs, t + 119);   check(v == V_MONITORING, "poll3 but grace unmet -> monitoring")
    v, e = evaluate(e, obs, t + 121);   check(v == V_WEDGE, "poll>=MIN + grace met -> WEDGE (safe)")

    print("Real staged draft on a lane -> WEDGE-UNSAFE (alert-only, never nudge)")
    e = None
    ro = mk(agent="cc-irsyad", kind="lane", session="irsyad", comp=COMP_REAL, text="apply the migration")
    for dt in (0, 60, 121):
        v, e = evaluate(e, ro, t + dt)
    check(v == V_WEDGE_UNSAFE, "real composer -> wedge-unsafe")

    print("Draining (unread drops) ends the episode")
    e = None
    for dt in (0, 60, 121):
        v, e = evaluate(e, obs, t + dt)
    check(v == V_WEDGE, "wedged")
    v, e = evaluate(e, mk(unread=0, oldest=0, last_write=1e9), t + 180)
    check(v == V_HEALTHY and "poll_count" not in e, "drained -> healthy, episode cleared")

    print("A self-write (goes non-quiet) mid-episode ends it too")
    e = None
    for dt in (0, 60, 121):
        v, e = evaluate(e, obs, t + dt)
    v, e = evaluate(e, mk(unread=3, oldest=1800, last_write=30), t + 180)
    check(v == V_HEALTHY, "agent wrote again -> healthy")

    print("Unreachable clears the episode")
    v, e = evaluate({"sig": "x", "poll_count": 5}, AgentObs(
        "l", "lane", "l", BusSignal(3, 1800, 1800), ComposerSignal(COMP_EMPTY), reachable=False), t)
    check(v == V_UNREACHABLE and "poll_count" not in e, "unreachable clears episode")

    print("End-to-end run(): DETECT-ONLY injected wedge -> WOULD-nudge, no action")
    os.environ["LANE_WEDGE_ALERT_STDOUT"] = "1"
    _calls = []
    global do_nudge
    _orig = do_nudge
    do_nudge = lambda o: (_calls.append(o.agent), (True, "stub"))[1]  # noqa: E731
    try:
        with tempfile.TemporaryDirectory() as td:
            STATE_FILE = Path(td) / "s.json"
            STATE_FILE.write_text(json.dumps({
                "agents": {"cai": {"sig": "3|delegated", "first_seen": t - 999,
                                   "poll_count": WEDGE_MIN_POLLS - 1, "last_seen": t - 60}},
                "wedge_history": {}, "deadman": {"last_beat": time.time()}}))
            rc = run(mode=MODE_DETECT, alert=False,
                     injected=[mk()], lane_dirs={}, persist=True)
            check(rc == 0, "run() returns 0 on a detect wedge scan")
            check(_calls == [], "detect-only fired NO nudge action")
    finally:
        do_nudge = _orig

    print()
    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} failure(s))")
        return 1
    print("SELF-TEST PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fleet idle-composer / stopped-draining wedge watchdog.")
    ap.add_argument("--arm", nargs="?", const="escalate", choices=["nudge", "escalate"],
                    default=None,
                    help="ARM recovery: --arm=nudge = auto-nudge only; --arm (=escalate) = full "
                         "ladder (nudge -> reset lanes / page singletons). Absent = detect-only. "
                         "Lease-gated. Overrides env LANE_WEDGE_ARM.")
    ap.add_argument("--alert", action="store_true",
                    help="page the operator (nazim-console) on a wedge / repeat-wedge / watchdog-down gap")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--once", action="store_true", help="single scan (default; explicit for tests)")
    ap.add_argument("--loop", action="store_true", help="self-cadenced loop (LANE_WEDGE_LOOP_INTERVAL_SEC)")
    ap.add_argument("--interval", type=int, default=None, help="loop interval seconds (with --loop)")
    ap.add_argument("--self-test", action="store_true",
                    help="offline state-machine checks (no DB, no tmux, no paging)")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    mode = resolve_mode(args.arm)
    if args.loop:
        interval = args.interval or LOOP_INTERVAL_SEC
        log(f"loop mode: interval={interval}s mode={mode} alert={args.alert}")
        while True:
            try:
                run(mode=mode, alert=args.alert, as_json=args.json)
            except Exception as e:  # a loop iteration must never kill the loop
                import traceback
                traceback.print_exc()
                _page(f"🐛 Lane-wedge watchdog loop iteration crashed: {e}. Still looping; fix soon.")
            time.sleep(interval)
    return run(mode=mode, alert=args.alert, as_json=args.json)


if __name__ == "__main__":
    # Dead-man's-switch: a watchdog that dies silently is worse than none. Any
    # unhandled throw pages via the dependency-free subprocess path (does NOT
    # import nervous_system) so the failure of the guard surfaces itself.
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
                 f"🐛 Lane-wedge watchdog CRASHED — it is NOT catching wedged agents right now: {_e}. "
                 f"Fix before relying on it."],
                timeout=30, cwd=str(_ORCH_DIR))
        except Exception:
            pass
        sys.exit(1)
