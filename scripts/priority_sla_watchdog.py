#!/usr/bin/env python3
"""priority_sla_watchdog.py — ACTION + escalation layer for inbox SLA violations.

THE PROBLEM it solves: a P0/P1 bus message can sit unhandled and silently
stall the fleet (2026-07-14: a P1 sat unread ~2h and stalled the operator's
go-live). Detection already exists — the `inbox_sla_violations` view surfaces
unread/unresponded `agent_messages` past their per-priority `priority_thresholds`
alarm. This daemon is the layer that ACTS on those violations: it re-nudges the
target agent, backs off, and escalates to the operator (via Nazim's voice) ONLY
when a fresh P0/P1 persists past a hard threshold AND a re-nudge already failed
to clear it.

DESIGN INVARIANT (the #1 correctness requirement): a mis-firing watchdog that
spams nudges or pages the operator repeatedly is WORSE than none. Every action
is gated by (a) a recency window — historical backlog is never actioned,
(b) per-message re-nudge backoff, (c) page-once-ever dedup enforced on BOTH a
durable state file AND the bus audit log, (d) a per-run page cap, and (e) an
abnormal-volume circuit breaker that suppresses ALL action and sends one summary
instead. On-record: every real action writes an attributable `agent_messages`
row from_agent='sla-watchdog' — the watchdog never acts off the record.

SCOPE NOTES / KNOWN CALIBRATION POINTS (see report for Nazim):
  * The view can only surface P1/P2/P3 (INNER JOIN priority_thresholds, which has
    no P0 row). P0 is surfaced by a SUPPLEMENTAL direct query here so P0 is never
    structurally invisible. Adding a P0 row to priority_thresholds would let the
    view surface it natively (recommended follow-up).
  * The view's P1 unread floor is 60 min (unresponded 240 min). Escalation is
    downstream of the view, so effective P1 escalation latency = max(view floor,
    HARD_ESCALATE_MIN). To page P1 faster than 60 min, lower
    priority_thresholds.unread_alarm_minutes (not this file).

Mirrors the lane_watchdog.py conventions: psycopg via SUPABASE_DB_URL/DATABASE_URL
in .env, logs/ file logging, a JSON state file for cross-scan persistence.
Designed for a ~60-90s launchd cadence (dev.wingmen.priority-sla-watchdog).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
STATE_FILE = ORCH / "logs" / "priority_sla_watchdog_state.json"
LOG_FILE = ORCH / "logs" / "priority_sla_watchdog.log"

sys.path.insert(0, str(ORCH))

# CAI-RESP-501: SLA escalation is the watchdog pen (iii) ACTING — gated on the
# fleet_health_lease single-owner lease (default holder cc-fleet-health; hub
# reclaims on expiry) so the SRE and a reclaiming hub never double-nudge/page.
from scripts.lib import fleet_health_lease  # noqa: E402
from scripts.lib import fire_window  # noqa: E402  (quiesce during a recycle fire window)

# ---------------------------------------------------------------------------
# Config — all overridable via environment (launchd EnvironmentVariables).
# ---------------------------------------------------------------------------

def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Only act on violations whose message was created within this window. The
# `inbox_sla_violations` view holds ~1000+ rows, almost all 26-46 DAYS old
# (rulings to 'down' agents), PLUS a chronic hub-inbox tail: cc-orchestrator
# alone carries ~110 unread in the 12-24h band (the hub receives everything and
# rarely mark_reads — same pattern lane_watchdog notes for lanes). A re-nudge
# can't clear either, and paging on them would blast the operator. The stall
# class we defend against is FRESH work that just crossed its SLA. Measured
# 2026-07-14: within 3h there are ~6 violations; within 24h there are ~131. So
# 3h cleanly isolates fresh stalls from chronic backlog. This is the primary
# spam-guard — widen only deliberately (and watch the circuit breaker).
MAX_VIOLATION_AGE_MIN = _envint("SLA_MAX_VIOLATION_AGE_MIN", 180)  # 3h

# Re-nudge the SAME (agent,message) at most once per this window.
RENUDGE_BACKOFF_MIN = _envint("SLA_RENUDGE_BACKOFF_MIN", 15)

# Operator-page HARD thresholds (minutes since message created), per priority.
HARD_ESCALATE_MIN = {
    "P0": _envint("SLA_HARD_ESCALATE_P0_MIN", 10),
    "P1": _envint("SLA_HARD_ESCALATE_P1_MIN", 20),
}
# Priorities eligible for an operator page. P2/P3 NEVER page (re-nudge only).
ESCALATE_PRIORITIES = {"P0", "P1"}

# A page fires only after this many re-nudge attempts have already been made in
# prior cycles (>=1 == "at least one re-nudge failed to clear it").
MIN_NUDGES_BEFORE_ESCALATE = _envint("SLA_MIN_NUDGES_BEFORE_ESCALATE", 1)

# Hard cap on operator pages emitted in a single run (defence-in-depth vs a
# logic bug). Excess is logged and held.
MAX_PAGES_PER_RUN = _envint("SLA_MAX_PAGES_PER_RUN", 3)

# Circuit breaker: if the ACTIONABLE (recency-windowed) violation count exceeds
# this, something is wrong (misconfig / mass-regression). Suppress ALL per-item
# action and emit ONE summary alert instead of a storm.
CIRCUIT_BREAKER_MAX = _envint("SLA_CIRCUIT_BREAKER_MAX", 25)

# The circuit-breaker page is OPERATOR-FACING (their phone). Without a re-page backoff
# it fired EVERY ~90s run while tripped -> phone spam (op#13980/2/4: 3 identical pages
# in 3 min during the Irsyad deadline, #25147). Page ONCE per tripped episode, then stay
# quiet for this long (the situation is durable — a bounded backlog does not move in 90s).
CB_REPAGE_MIN = _envint("SLA_CB_REPAGE_MIN", 60)

# P0 supplemental-query surfacing floor (view can't surface P0). A P0 becomes
# actionable once elapsed exceeds this (defaults to the P0 hard threshold).
P0_SURFACE_MIN = _envint("SLA_P0_SURFACE_MIN", HARD_ESCALATE_MIN["P0"])

# Agents to drop from actioning entirely (comma-separated). Lever for Nazim to
# exclude e.g. the operator-attended hub itself if paging on the hub's own
# chronic unread proves circular/noisy. Empty by default (nothing excluded).
# A message an agent sent to ITSELF is a NOTE, not a coordination stall — nobody else can
# action it and nobody is waiting on it. Left in, it escalates: and because the escalation
# ladder treats "recipient == orch-console" as "the internal path IS the stall", a self-note
# would fall straight through to an OPERATOR PAGE. I filed exactly such a note at 01:2xZ as a
# durable reminder, while the operator was DRIVING, and it would have paged him within the
# hour — the same noise the ladder was built an hour earlier to stop.
def is_self_note(v: dict) -> bool:
    return bool(v.get("from_agent")) and v.get("from_agent") == v.get("agent")


EXCLUDE_AGENTS = {
    a.strip() for a in os.environ.get("SLA_EXCLUDE_AGENTS", "").split(",") if a.strip()
}

# Prune state entries not seen for this long.
STATE_TTL_SEC = 7 * 24 * 3600

# Hub agents unreachable via lane_nudge — reached by count-only tmux send-keys on
# the Studio (ssh). Session names are best-effort; flag if wrong.
STUDIO_SSH = os.environ.get("SLA_STUDIO_SSH", "Musa@mac-studio")
# Full path to tmux on the Studio — a NON-interactive ssh shell (zsh) does NOT
# have /opt/homebrew/bin on PATH, so a bare `tmux` over ssh fails ("command not
# found") and every hub-nudge silently no-ops (then escalates to paging the
# operator). Diagnosed 2026-07-17. Override via SLA_REMOTE_TMUX if the Studio
# moves tmux.
REMOTE_TMUX = os.environ.get("SLA_REMOTE_TMUX", "/opt/homebrew/bin/tmux")
HUB_SESSIONS = {
    "cc-orchestrator": os.environ.get("SLA_HUB_SESSION_ORCH", "orch"),
    "cc-infra": os.environ.get("SLA_HUB_SESSION_INFRA", "infra"),
}

# op#11774 #5 INTERIM (ship-first, page-only): the read-parked-hub grace. A hub that
# READS a P0/P1 rr row then PARKS (responded_at NULL) is silenced by attended_for()'s
# read==attending rule; this net surfaces it after INTERIM_PARK_MIN. Chosen LONGER
# than legit VPS wake-latency (>20m, why the suppression exists) so a slow-waking-but-
# working hub isn't false-paged, but WELL short of the hours 18492/18537 actually sat.
# The Phase-1 oracle version (suppress only if oracle=WORKING) removes the fixed number.
INTERIM_PARK_MIN = int(os.environ.get("SLA_HUB_PARK_MIN", "60"))
# Upper bound (CRITICAL — found live 2026-08-11): the hub CHRONICALLY leaves
# responded_at unstamped even after acting, so an unbounded responded-NULL predicate
# backfills days-old cruft (a first dry-run flagged 30+ historical rows). This bound
# keeps it a RECENT-park net: only escalate a park aged between MIN and MAX, never
# replay history. The oracle version (Phase 1, suppress-only-if-WORKING) removes the
# reliance on responded_at entirely.
INTERIM_PARK_MAX = int(os.environ.get("SLA_HUB_PARK_MAX", "360"))
# Cascade-guard: a hard cap on read-parked escalations per scan so a future misfire
# can NEVER flood (the op#11774 incident fired ~90 in one scan). Combined with the
# watermark + dedup, this bounds the blast radius of any bug to N.
MAX_READ_PARKED_PER_SCAN = int(os.environ.get("SLA_READ_PARKED_MAX_PER_SCAN", "3"))


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


def _dsn() -> str:
    from dotenv import load_dotenv
    load_dotenv(str(ORCH / ".env"))
    return os.environ.get("SUPABASE_DB_URL") or os.environ["DATABASE_URL"]


def _connect():
    import psycopg
    return psycopg.connect(_dsn(), connect_timeout=15)


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text())
        if "msgs" not in s:
            s["msgs"] = {}
        return s
    except Exception:
        return {"msgs": {}}


def save_state(state: dict) -> None:
    now = time.time()
    # prune stale entries
    state["msgs"] = {
        k: v for k, v in state.get("msgs", {}).items()
        if now - v.get("last_seen_ts", now) < STATE_TTL_SEC
    }
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except Exception as e:
        log(f"state-write-failed: {e}")


def circuit_breaker_should_page(cb: dict, now: float, backoff_min: int) -> bool:
    """True if the operator-facing circuit-breaker page is due. The breaker trips on
    every ~90s run while the volume is high; page only ONCE per episode, then honor a
    re-page backoff (default 60m) so the operator's phone is not spammed with identical
    'manual review needed' pages. A first trip (no prior page) always pages."""
    last = (cb or {}).get("last_paged_ts", 0) or 0
    return (now - last) >= backoff_min * 60


# ---------------------------------------------------------------------------
# Detection — consume the view + P0 supplement, filter to the recency window.
# ---------------------------------------------------------------------------

def fetch_actionable(conn) -> list[dict]:
    """Return violations within the recency window, from the view + P0 supplement.

    De-duplicated to one row per (message_id, violation_type). The view already
    encodes detection; we only add the recency filter and the P0 supplement
    (which the view's INNER JOIN on priority_thresholds structurally omits).
    """
    out: list[dict] = []
    with conn.cursor() as cur:
        # (1) The canonical view — P1/P2/P3.
        cur.execute(
            """
            SELECT agent, message_id, priority, from_agent, subject,
                   violation_type, elapsed_minutes, threshold_minutes
              FROM inbox_sla_violations
             WHERE elapsed_minutes <= %s
            """,
            (MAX_VIOLATION_AGE_MIN,),
        )
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            row = dict(zip(cols, r))
            if not is_self_note(row):
                out.append(row)

        # (2) P0 supplement — the view cannot surface P0 (no threshold row).
        # Mirror the view's violation semantics for priority='P0' only.
        cur.execute(
            """
            WITH m AS (
              SELECT id, to_agent, from_agent, subject, requires_response,
                     read_at, responded_at,
                     (EXTRACT(epoch FROM now()-created_at)/60.0)::int AS elapsed
                FROM agent_messages
               WHERE priority='P0' AND is_test IS NOT TRUE
                 AND (read_at IS NULL OR (requires_response AND responded_at IS NULL))
                 AND created_at > now() - (%s * interval '1 minute')
            )
            SELECT to_agent, id, 'P0', from_agent, subject, 'unread', elapsed, %s
              FROM m WHERE read_at IS NULL AND elapsed > %s
            UNION ALL
            SELECT to_agent, id, 'P0', from_agent, subject, 'unresponded', elapsed, %s
              FROM m WHERE requires_response AND responded_at IS NULL AND elapsed > %s
            """,
            (MAX_VIOLATION_AGE_MIN, P0_SURFACE_MIN, P0_SURFACE_MIN,
             P0_SURFACE_MIN, P0_SURFACE_MIN),
        )
        p0cols = ["agent", "message_id", "priority", "from_agent", "subject",
                  "violation_type", "elapsed_minutes", "threshold_minutes"]
        for r in cur.fetchall():
            out.append(dict(zip(p0cols, r)))

    # de-dupe by (message_id, violation_type); drop excluded agents
    seen = {}
    for v in out:
        if v["agent"] in EXCLUDE_AGENTS:
            continue
        # The watchdog never re-nudges about its OWN rows — incl. the hub bus-wake
        # rows it posts (#16246). Without this, a requires_response wake row to the
        # hub would itself read as an unresponded-P1 violation next scan and cascade.
        if v.get("from_agent") == "sla-watchdog":
            continue
        seen[(v["message_id"], v["violation_type"])] = v
    return list(seen.values())


def lane_map(conn) -> dict[str, str]:
    """base_agent_id -> tmux lane (from fleet_lanes)."""
    with conn.cursor() as cur:
        cur.execute("SELECT base_agent_id, lane FROM fleet_lanes WHERE base_agent_id IS NOT NULL")
        return {a: l for a, l in cur.fetchall()}


# ---------------------------------------------------------------------------
# Re-nudge routing. Every nudge is COUNT-ONLY (never message content / operator
# words) — the phantom-injection-safe form (matches nudge_cai.sh /
# nudge_orch_escalations). Returns (mechanism, ok).
# ---------------------------------------------------------------------------

def _countonly_line(agent: str, n: int) -> str:
    return (f"\U0001F4E5 {n} priority bus message(s) addressed to you are past SLA — "
            f"read your agent_messages inbox and action/route each (mark read).")


def _tmux_countonly(session: str, line: str, host: str | None = None) -> bool:
    """Count-only send-keys into a tmux session (optionally over ssh to the
    Studio). Clears any stuck draft (C-u) first; only fires if the pane is idle
    (never interrupts a working session). Best-effort.

    Over ssh the remote runs a NON-interactive shell (zsh on the Studio): a bare
    `tmux` is not on its PATH, and an unquoted `=orch` target triggers zsh's
    `=`-filename-expansion ("orch not found") — both silently break the nudge.
    So for the remote case we send ONE command string that (a) uses the full
    tmux path and (b) single-quotes every arg so the target survives verbatim."""
    def tm(*args: str) -> subprocess.CompletedProcess:
        if host:
            # force-single-quote each arg so zsh leaves `=orch`/the line literal
            remote = " ".join([REMOTE_TMUX] + ["'" + a.replace("'", "'\\''") + "'" for a in args])
            base = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, remote]
        else:
            base = ["tmux", *args]
        return subprocess.run(base, capture_output=True, text=True, timeout=25)
    # A recycle owns this pane for a few seconds; typing inside that window jams the
    # /clear and the body comes back half-initialised. Skipping is free — the bus row
    # is durable and the fresh body reconciles it at boot. Local sessions only: the
    # lock lives on the host that runs the reset.
    if host is None and fire_window.is_held(session):
        return False
    try:
        if tm("has-session", "-t", f"={session}").returncode != 0:
            return False  # no such session on that host
        cap = tm("capture-pane", "-t", f"={session}:0.0", "-p").stdout.lower()
        if "esc to interrupt" in cap:
            return False  # working — do not disturb (still counts as attempt)
        tm("send-keys", "-t", f"={session}:0.0", "C-u")
        time.sleep(0.3)
        tm("send-keys", "-t", f"={session}:0.0", "-l", line)
        time.sleep(0.4)
        tm("send-keys", "-t", f"={session}:0.0", "Enter")
        return True
    except Exception:
        return False


def renudge(agent: str, n: int, lanes: dict[str, str], dry: bool, conn=None) -> tuple[str, bool]:
    """Route a count-only re-nudge to `agent`. Returns (mechanism, ok).

    An attempt is always recorded by the caller regardless of ok — an
    unreachable/failed nudge still counts as "a re-nudge that didn't clear it",
    which is exactly the signal that should lead to an operator escalation.
    """
    line = _countonly_line(agent, n)

    if agent == "musa":
        return ("operator-not-nudgeable", False)  # operator isn't an agent lane
    if agent == "cai":
        if dry:
            return ("nudge_cai.sh", True)
        # nudge_cai.sh exits 0 with "no live cai session" when cai's tmux isn't
        # on THIS host — so returncode==0 alone is NOT proof of reach. cai lives
        # on the Studio; this watchdog runs on the Mini. Try local, then run the
        # nudger ON the Studio over ssh (its non-interactive PATH needs
        # /opt/homebrew/bin for tmux; the script sources its own .env + venv).
        def _reached_cai(argv) -> bool:
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=45)
                out = (r.stdout + r.stderr).lower()
                return r.returncode == 0 and "no live cai session" not in out
            except Exception:
                return False
        if _reached_cai([str(ORCH / "scripts" / "nudge_cai.sh")]):
            return ("nudge_cai.sh", True)
        remote = ("cd ~/wingmen/orchestrator && "
                  "PATH=/opt/homebrew/bin:$PATH bash scripts/nudge_cai.sh")
        ok = _reached_cai(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
                           STUDIO_SSH, remote])
        return ("nudge_cai.sh@studio", ok)
    if agent in HUB_SESSIONS:
        # The hub relocated to the VPS (Studio stood down), so the old studio-tmux
        # send-keys are DEAD (#16246). The VPS hub is woken by the DB-driven
        # agent_wake_subscriber, not tmux — so re-nudge it by POSTING a wake-contract
        # bus row (CAI-RESP-451 / hub #16415): to_agent=hub, requires_response=true,
        # priority P0/P1, is_test=false, actionable type -> the subscriber fires and
        # wakes it (the hub now self-registers agent_status + is protected from the
        # reaper, so resolve_tmux_session succeeds). from_agent='sla-watchdog' is
        # excluded from violation detection, so this wake row never cascades.
        if dry or conn is None:
            return ("bus-wake:%s" % agent, True)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_messages
                       (from_agent, to_agent, message_type, subject, body,
                        requires_response, priority, is_test)
                       VALUES ('sla-watchdog', %s, 'blocker', %s, %s, true, 'P1', false)""",
                    (agent,
                     "[sla-watchdog] WAKE: %s has %d over-SLA unread/unresponded high-priority message(s)" % (agent, n),
                     "You have over-SLA unread or unresponded P0/P1 bus message(s). Please check "
                     "your inbox and act/respond. (Automated SLA wake — the VPS agent_wake_subscriber "
                     "fires on this row; the watchdog does not track its own rows, so this will not "
                     "recur once you drain them.)"))
            conn.commit()
            return ("bus-wake:%s" % agent, True)
        except Exception as e:
            log("hub bus-wake failed (%s): %s" % (agent, e))
            try:
                conn.rollback()
            except Exception:
                pass
            return ("bus-wake:%s" % agent, False)
    if agent in lanes:
        lane = lanes[agent]
        if dry:
            return (f"lane_nudge.sh:{lane}", True)
        try:
            r = subprocess.run([str(ORCH / "scripts" / "lane_nudge.sh"), lane, line],
                               capture_output=True, text=True, timeout=90)
            return (f"lane_nudge.sh:{lane}", r.returncode == 0)
        except Exception:
            return (f"lane_nudge.sh:{lane}", False)
    return ("unreachable", False)


# ---------------------------------------------------------------------------
# Escalation — operator page via Nazim's voice + alert_format ELI5.
# ---------------------------------------------------------------------------

def build_page(v: dict, nudges: int) -> str:
    from nervous_system.alert_format import format_alert
    agent = v["agent"]
    mid = v["message_id"]
    pr = v["priority"]
    elapsed = v["elapsed_minutes"]
    vtype = v["violation_type"]
    subject = (v.get("subject") or "")[:90]
    from_agent = v.get("from_agent")

    # cai is the SINGLETON governance node — it carries ~55% of fleet rulings
    # with NO failover, so a stalled/absent cai is NOT a routine stuck lane: it
    # means the fleet's ruling path is DOWN and everything awaiting a ruling is
    # now unqueueable. Page it distinctly so the operator reads "governance queue
    # stalled", not "an engineer lane is slow". Page-template ONLY — reviving cai
    # is a SEPARATE, gated executor (CAI-510); this must NOT touch that.
    if agent == "cai":
        return format_alert(
            icon="\U0001F6A8",
            title="GOVERNANCE QUEUE STALLED — no live cai ruling path",
            what=(f"cai (the fleet's governance node) has a {pr} ruling request "
                  f"(#{mid} from {from_agent}) {vtype} for {elapsed} min and a "
                  f"re-nudge did not reach a live cai session — items awaiting a "
                  f"ruling are now unqueueable."),
            why=("cai carries ~55% of fleet rulings and has NO failover — while "
                 "its queue is stalled, every gate/decision behind it is blocked "
                 "(a fleet-wide governance outage, not one slow engineer lane)."),
            do=("Confirm cai has a live session (boot via scripts/boot_cai.sh if "
                f"absent), then action ruling #{mid}, or hand it to the "
                "operator/hub as tie-breaker."),
            detail=(f"agent=cai msg_id={mid} type={vtype} elapsed={elapsed}m "
                    f"prior_nudges={nudges} subject={subject!r}"),
            ref="PRIORITY-SLA-WATCHDOG / GOVERNANCE-QUEUE-STALLED",
        )

    return format_alert(
        icon="\U0001F6A8",
        title=f"{pr} bus message stuck — {agent} not responding",
        what=(f"{pr} message #{mid} from {from_agent} to {agent} has been "
              f"{vtype} for {elapsed} min and a re-nudge did not clear it."),
        why=("P0/P1 is time-sensitive — an unhandled one silently stalls the "
             "fleet (this is the class that cost ~2h on a go-live)."),
        do=(f"Open {agent}'s session and action msg #{mid}, or reassign it."),
        detail=(f"agent={agent} msg_id={mid} type={vtype} elapsed={elapsed}m "
                f"prior_nudges={nudges} subject={subject!r}"),
        ref="PRIORITY-SLA-WATCHDOG",
    )


# ---------------------------------------------------------------------------
# ESCALATION LADDER (CAI-600, 2026-07-26). A stalled AGENT queue is an
# agent-coordination problem: the operator cannot clear another body's inbox, and
# paging him converts coordination into noise. It happened at 00:35Z — cai's queue
# stalled while cai was deliberately offline mid-wrap-up, and the page went to the
# operator WHILE HE WAS DRIVING. Factually correct, operationally useless.
#
# So: escalate INTERNALLY first. orch-console (Nazim) is the body that can chase a
# stalled agent, re-route the item, or wake the recipient. The operator is paged
# ONLY when there is no internal path left — i.e. orch-console itself is the
# stalled party or is unreachable — or when the item requires HIM specifically.
#
# Fails toward paging: if we cannot determine whether the internal path is alive,
# the operator still gets it. A missed page is worse than an unnecessary one; this
# only removes pages we can PROVE somebody else can act on.
def _internal_path_alive(recipient: str) -> bool:
    """Is orch-console live and NOT itself the stalled recipient?"""
    if recipient == "orch-console":
        return False                      # the internal escalator IS the stall
    tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
    try:
        r = subprocess.run([tmux, "has-session", "-t", "=nazim"],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:                      # noqa: BLE001 — cannot tell => page
        return False


def escalate_internally(v: dict, nudges: int) -> bool:
    """File the stall to orch-console instead of paging the operator."""
    body = (f"SLA stall on an agent queue — routed to you rather than the operator, who cannot "
            f"clear another body's inbox (CAI-600).\n\n"
            f"  recipient : {v['agent']}\n  message   : #{v['message_id']} ({v['priority']})\n"
            f"  stalled   : {v['elapsed_minutes']}m, {nudges} prior re-nudge(s) failed\n"
            f"  subject   : {(v.get('subject') or '')[:120]!r}\n\n"
            f"Chase the recipient, re-route the item, or tell me why it is correctly waiting. "
            f"If this genuinely needs the operator, escalate it yourself with the reason — that "
            f"judgement is yours, not this watchdog's.")
    try:
        import psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
                "priority) VALUES ('sla-watchdog','orch-console','blocker',%s,%s,%s)",
                (f"SLA stall: {v['agent']} #{v['message_id']} ({v['priority']}, "
                 f"{v['elapsed_minutes']}m)", body, v["priority"]))
            conn.commit()
        return True
    except Exception as e:                 # noqa: BLE001
        log(f"internal escalation failed ({e}) — falling through to operator page")
        return False


def send_page(text: str, dry: bool) -> bool:
    if dry:
        return True
    try:
        r = subprocess.run([str(ORCH / "scripts" / "nazim_send.sh"), text],
                           capture_output=True, text=True, timeout=40)
        if r.returncode != 0:
            log(f"nazim_send failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        return r.returncode == 0
    except Exception as e:
        log(f"nazim_send exception: {e}")
        return False


# ---------------------------------------------------------------------------
# On-record audit — attributable agent_messages rows from_agent='sla-watchdog'.
# read_at=now() so the watchdog's own audit rows never become violations.
# ---------------------------------------------------------------------------

def ensure_identity(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agents(id, display_name, status) "
            "VALUES('sla-watchdog','SLA Watchdog (priority-SLA escalation daemon)','active') "
            "ON CONFLICT (id) DO NOTHING"
        )
    conn.commit()


def audit(conn, subject: str, body: str, priority: str = "P3") -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_messages
                   (from_agent, to_agent, message_type, subject, body,
                    requires_response, priority, read_at)
                   VALUES('sla-watchdog','cc-orchestrator','update',%s,%s,false,%s,now())""",
                (subject[:200], body, priority),
            )
        conn.commit()
    except Exception as e:
        log(f"audit-insert-failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


def already_paged_on_bus(conn, mid: int) -> bool:
    """Belt-and-suspenders page dedup: even if the state file is lost, never
    double-page — check the durable bus audit log for a prior PAGE row."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_messages WHERE from_agent='sla-watchdog' "
                "AND subject LIKE %s LIMIT 1",
                (f"[sla-watchdog] PAGED operator re msg #{mid}%",),
            )
            return cur.fetchone() is not None
    except Exception as e:
        log(f"bus-page-dedup-check-failed #{mid}: {e}")
        return False  # fail-open toward paging (a rare double-page beats a miss)


def attended_for(conn, mid: int, violation_type: str, agent: str = "") -> bool:
    """Re-check at PAGE time whether the row has since been attended, in a way
    that matches its violation_type. `fetch_actionable` snapshots violations at
    scan start and the view flags 'unread' purely on read_at; an owner who
    RESPONDS (stamps responded_at) without stamping read_at is plainly attending
    the row, yet the stale unread flag would still page the operator (false-paged
    #8723: hub set responded_at on a requires_response=False FYI, the unread flag
    lingered, operator got noise).

    Rules (type-aware, so we suppress noise WITHOUT masking a real stall):
      * responded_at set  -> ALWAYS attended (the owner acted on it) — never page.
      * violation is 'unread' and read_at set -> the unread condition itself has
        cleared — never page.
      * violation is 'unresponded' and only read_at set (no responded_at) -> NOT
        suppressed for a normal lane: 'seen but not answered' past the hard
        threshold is exactly the stall the operator SHOULD hear about.
      * EXCEPTION — HUB recipient (op#11565 f/u, orch-console #17569): the hub
        (cc-orchestrator/cc-infra) self-wakes on a SLOWER cadence than a nudged
        lane — it READS a requires_response row and actively works it for a while
        before stamping responded_at. Its VPS auto-wake latency exceeds the P1
        hard threshold (20m), so 'read + working' hub rows false-paged the operator
        (#17548/#17559, both drained by check-time). For hub recipients ONLY,
        read_at set == attending -> suppress. A hub row NOT EVEN READ past the
        threshold still pages — that's the real 'hub not woken' alarm (#16246),
        deliberately preserved.
    Fail-OPEN toward paging on error (a rare double-page beats missing a real P0)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT read_at IS NOT NULL, responded_at IS NOT NULL "
                "FROM agent_messages WHERE id=%s",
                (mid,),
            )
            row = cur.fetchone()
            if not row:
                return False
            is_read, is_resp = row
            if is_resp:
                return True
            if violation_type == "unread" and is_read:
                return True
            if agent in HUB_SESSIONS and is_read:
                return True
            return False
    except Exception as e:
        log(f"attended-recheck-failed #{mid}: {e}")
        return False


# ---------------------------------------------------------------------------
# op#11774 #5 INTERIM — escalate a READ-but-PARKED hub (page-only safety net)
# ---------------------------------------------------------------------------
def read_parked_hub_targets(rows, *, interim_min: int = INTERIM_PARK_MIN,
                            interim_max: int = INTERIM_PARK_MAX,
                            watermark_id: int = 0):
    """PURE: hub P0/P1 requires_response rows that are READ, UNRESPONDED, aged
    BETWEEN interim_min and interim_max, and NEWER than watermark_id. Two guards
    against the op#11774 flood: (1) the upper bound — responded_at is chronically
    unstamped so without it this backfills days-old cruft; (2) the BACKFILL WATERMARK
    — `id > watermark_id` excludes the entire pre-existing backlog by construction
    (the watermark is pinned to max(id) at enable-time), so un-hiding the suppressed
    backlog escalates ZERO of it; only stalls that cross the threshold GOING FORWARD
    page. Rows are dicts with keys id, to_agent, priority, requires_response, read_at,
    responded_at, elapsed_minutes. A hub row NOT read is the existing 'hub not woken'
    alarm (#16246); a normal lane is covered by the standard ladder — hub read-parked
    gap only."""
    out = []
    for r in rows:
        elapsed = r.get("elapsed_minutes") or 0
        if (r.get("to_agent") in HUB_SESSIONS
                and r.get("priority") in ("P0", "P1")
                and r.get("requires_response")
                and r.get("read_at") is not None
                and r.get("responded_at") is None
                and interim_min <= elapsed <= interim_max
                and (r.get("id") or 0) > watermark_id):
            out.append(r)
    return out


def _fetch_read_parked_hub(conn):
    """The read-parked-hub rows from the DB (impure). Bounded to the recent window
    (created within interim_max) so it never replays history; the pure predicate
    re-checks both bounds as defense-in-depth."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, to_agent, priority, requires_response, read_at, responded_at, "
            "  (EXTRACT(epoch FROM (now()-created_at))/60.0)::int AS elapsed_minutes "
            "FROM agent_messages "
            "WHERE to_agent = ANY(%s) AND priority IN ('P0','P1') AND requires_response "
            "  AND read_at IS NOT NULL AND responded_at IS NULL AND is_test IS NOT TRUE "
            "  AND created_at > now() - make_interval(mins => %s)",
            (list(HUB_SESSIONS.keys()), INTERIM_PARK_MAX))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def escalate_read_parked(targets, *, dry, already_paged, escalate_internally,
                         send_page, renudge=None, record=None, max_escalations=None):
    """For each read-parked hub target, escalate to a SUPERVISOR — internal
    (orch-console) first, else an operator page — so a human hears. NEVER renudge
    the parked body: nudging via the same broken wake path is useless (that is the
    whole point of #5). `renudge` is accepted only to make that invariant explicit;
    it is intentionally never called. Deduped via `already_paged`; `record(mid,
    outcome)` marks a fired escalation so a later scan dedups it. `max_escalations`
    is a hard per-scan cascade-guard — beyond it, targets are HELD (logged), never
    fired, so a future misfire cannot flood (op#11774). Page-only: no live-body
    keystroke on any path. Seams injected for testability."""
    results = []
    fired = 0
    for t in targets:
        mid = t.get("id")
        if max_escalations is not None and fired >= max_escalations:
            results.append((mid, "held-rate-limit"))
            continue
        if not dry and already_paged(mid):
            results.append((mid, "deduped"))
            continue
        if dry:
            results.append((mid, "dry"))
            continue
        summary = (
            f"HUB READ-PARKED (op#11774 #5): {t.get('priority')} rr msg #{mid} to "
            f"{t.get('to_agent')} was READ but is UNRESPONDED {t.get('elapsed_minutes')}m. "
            f"The body read it and parked — it needs a re-drive/action; nudging the wedged "
            f"body won't help. Chase/re-drive it, or say why it is correctly waiting.")
        outcome = "escalated" if escalate_internally(t) else ("paged" if send_page(summary) else "failed")
        if outcome != "failed":
            fired += 1
            if record:
                record(mid, outcome)
        results.append((mid, outcome))
    return results


def _v_from_target(t: dict) -> dict:
    """Shape a read-parked target into the `v` dict escalate_internally expects."""
    return {"agent": t.get("to_agent"), "message_id": t.get("id"),
            "priority": t.get("priority"), "elapsed_minutes": t.get("elapsed_minutes"),
            "subject": "(read-parked route)"}


def _max_message_id(conn) -> int:
    """Current max agent_messages id — the backfill-guard watermark, pinned at
    enable-time so the entire pre-existing backlog is id <= watermark (excluded)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(max(id), 0) FROM agent_messages")
            return int(cur.fetchone()[0])
    except Exception as e:
        log(f"read-parked-watermark-read-failed: {e}")
        # Fail SAFE toward NOT escalating: a huge sentinel means nothing is > it, so
        # a read failure suppresses the net rather than risking a backfill flood.
        return 1 << 62


def _already_escalated_read_parked(conn, mid: int) -> bool:
    """Dedup: has this read-parked row already been escalated (audit marker)? Fail
    OPEN toward escalating — a rare double beats missing a real parked P1."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_messages WHERE from_agent='sla-watchdog' "
                "AND subject LIKE %s LIMIT 1",
                (f"[sla-watchdog] READ-PARKED escalated #{mid}%",))
            return cur.fetchone() is not None
    except Exception as e:
        log(f"read-parked-dedup-check-failed #{mid}: {e}")
        return False


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def run(dry: bool, injected: list[dict] | None = None,
        persist: bool | None = None) -> int:
    # A live --dry-run must be side-effect-free on the PRODUCTION state file, or
    # a manual dry-run would record phantom nudge attempts and make the next real
    # run page early. Default: persist only on a real run. self_test overrides to
    # True (it points STATE_FILE at a throwaway temp file).
    if persist is None:
        persist = not dry
    state = load_state()
    now = time.time()
    actions: list[str] = []
    pages_this_run = 0

    try:
        conn = _connect()
    except Exception as e:
        log(f"db-connect-failed: {e}")
        return 1

    try:
        if not dry:
            # Pen gate (CAI-RESP-501): only the fleet_health_lease holder may
            # nudge/page. If a different live body holds the pen, DON'T act —
            # downgrade to dry-run (still scan + log what we WOULD do) so the
            # SRE and a reclaiming hub never double-page. Fail-safe otherwise
            # (missing/unreadable lease -> proceed; never strand SLA coverage).
            ok, why = fleet_health_lease.gate()
            if not ok:
                log(f"pen-gate: SLA watchdog DEFERRED (CAI-RESP-501) — {why}; "
                    f"downgrading to dry-run (no nudge/page this scan)")
                dry = True
                persist = False
        if not dry:
            ensure_identity(conn)
        lanes = lane_map(conn)
        violations = injected if injected is not None else fetch_actionable(conn)

        # ---- circuit breaker -------------------------------------------------
        if len(violations) > CIRCUIT_BREAKER_MAX:
            summary = (f"\U0001F6A8 SLA watchdog: {len(violations)} actionable violations "
                       f"(> circuit-breaker {CIRCUIT_BREAKER_MAX}). Auto-action SUPPRESSED "
                       f"— likely misconfig or mass-regression. Manual review needed.")
            log(f"CIRCUIT-BREAKER tripped: {len(violations)} > {CIRCUIT_BREAKER_MAX} — suppressing all action")
            cb = state.get("circuit_breaker") or {"last_paged_ts": 0}
            should_page = circuit_breaker_should_page(cb, now, CB_REPAGE_MIN)
            ago = int((now - (cb.get("last_paged_ts") or 0)) / 60)
            if dry:
                print("[DRY] CIRCUIT BREAKER — would " + (
                    "send ONE summary page" if should_page
                    else f"SUPPRESS the page (re-page backoff {CB_REPAGE_MIN}m; last paged {ago}m ago)")
                    + " and take no per-item action:")
                print("      " + summary)
            elif should_page:
                send_page(summary, dry=False)
                audit(conn, "[sla-watchdog] CIRCUIT BREAKER tripped", summary, priority="P1")
                cb["last_paged_ts"] = now
                state["circuit_breaker"] = cb
            else:
                log(f"CIRCUIT-BREAKER page SUPPRESSED (re-page backoff {CB_REPAGE_MIN}m; last paged {ago}m ago) "
                    "— operator already notified this episode")
            if persist:
                save_state(state)
            return 0

        # group by agent for count-only nudge lines (one line covers the agent's
        # whole over-SLA set, never per-message content)
        by_agent: dict[str, int] = {}
        for v in violations:
            by_agent[v["agent"]] = by_agent.get(v["agent"], 0) + 1

        # Process most-urgent-first so the per-run page cap serves P0 before P1,
        # and older-before-newer within a priority.
        rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        violations.sort(key=lambda x: (rank.get(x["priority"], 9), -x["elapsed_minutes"]))

        # Nudge each AGENT at most once per cycle. A single count-only nudge tells
        # the agent about its whole backlog; firing one keystroke per message would
        # spam the same session N times in one scan. Later messages for an
        # already-nudged agent reuse that result for accounting (no extra keystroke,
        # no duplicate audit row).
        nudged_this_cycle: dict[str, tuple[str, bool]] = {}

        for v in violations:
            mid = v["message_id"]
            agent = v["agent"]
            pr = v["priority"]
            elapsed = v["elapsed_minutes"]
            key = f"{mid}:{v['violation_type']}"
            rec = state["msgs"].get(key, {"nudge_count": 0, "last_nudge_ts": 0,
                                          "paged": False, "paged_ts": 0})
            rec["last_seen_ts"] = now
            prev_nudges = rec["nudge_count"]

            hard = HARD_ESCALATE_MIN.get(pr)
            escalate_eligible = (
                pr in ESCALATE_PRIORITIES
                and hard is not None
                and elapsed > hard
                and prev_nudges >= MIN_NUDGES_BEFORE_ESCALATE
                and not rec["paged"]
            )

            # Pre-page attended-recheck (fixes the #8723 false-page class): the
            # violation set was snapshotted at scan start and 'unread' keys on
            # read_at alone. If the owner has since read OR responded, the row is
            # attended — suppress the operator page. read_at/responded_at are
            # monotonic, so an attended row stays suppressed on later cycles too.
            if escalate_eligible and attended_for(conn, mid, v["violation_type"], agent):
                actions.append(
                    f"SKIP page #{mid} ({pr}) — attended (read/responded) since scan; suppressing operator page")
                escalate_eligible = False

            if escalate_eligible:
                # page-once dedup: state flag + durable bus check
                if not dry and already_paged_on_bus(conn, mid):
                    rec["paged"] = True
                    actions.append(f"SKIP page #{mid} ({pr}) — already paged on bus (dedup)")
                elif pages_this_run >= MAX_PAGES_PER_RUN:
                    actions.append(f"HOLD page #{mid} ({pr}) — per-run page cap {MAX_PAGES_PER_RUN} reached")
                elif _internal_path_alive(agent) and (dry or escalate_internally(v, prev_nudges)):
                    # CAI-600: an agent-queue stall goes to orch-console, not the operator.
                    # He cannot clear another body's inbox; paging him turns coordination into
                    # noise (00:35Z: cai's queue paged him WHILE HE WAS DRIVING). Only reached
                    # when the internal path is provably alive AND the bus write succeeded —
                    # any doubt falls through to the operator page below.
                    rec["paged"] = True
                    rec["paged_ts"] = now
                    actions.append(
                        f"{'[DRY] ' if dry else ''}INTERNAL escalation re #{mid} "
                        f"({pr} {v['violation_type']}, {elapsed}m, agent={agent}) -> orch-console "
                        f"(operator NOT paged: he cannot clear it)")
                else:
                    page_text = build_page(v, prev_nudges)
                    ok = send_page(page_text, dry)
                    pages_this_run += 1
                    rec["paged"] = True
                    rec["paged_ts"] = now
                    actions.append(
                        f"{'[DRY] ' if dry else ''}PAGE operator re #{mid} ({pr} {v['violation_type']}, "
                        f"{elapsed}m, agent={agent}, prior_nudges={prev_nudges}) ok={ok}")
                    if not dry:
                        audit(conn, f"[sla-watchdog] PAGED operator re msg #{mid}",
                              page_text, priority="P1")
            else:
                # re-nudge unless within backoff
                within_backoff = (now - rec["last_nudge_ts"]) < RENUDGE_BACKOFF_MIN * 60
                if within_backoff and prev_nudges > 0:
                    actions.append(
                        f"SKIP nudge #{mid} ({pr}) agent={agent} — backoff "
                        f"({int((now-rec['last_nudge_ts'])/60)}m < {RENUDGE_BACKOFF_MIN}m)")
                elif agent in nudged_this_cycle:
                    # agent already nudged this scan — coalesce (no 2nd keystroke)
                    mech, ok = nudged_this_cycle[agent]
                    rec["nudge_count"] += 1
                    rec["last_nudge_ts"] = now
                    actions.append(
                        f"{'[DRY] ' if dry else ''}NUDGE #{mid} ({pr} {v['violation_type']}, "
                        f"{elapsed}m) agent={agent} via {mech} (coalesced, no 2nd keystroke) "
                        f"(attempt {rec['nudge_count']})")
                else:
                    mech, ok = renudge(agent, by_agent[agent], lanes, dry, conn)
                    nudged_this_cycle[agent] = (mech, ok)
                    rec["nudge_count"] += 1
                    rec["last_nudge_ts"] = now
                    actions.append(
                        f"{'[DRY] ' if dry else ''}NUDGE #{mid} ({pr} {v['violation_type']}, "
                        f"{elapsed}m) agent={agent} via {mech} ok={ok} "
                        f"(attempt {rec['nudge_count']})")
                    if not dry:
                        audit(conn,
                              f"[sla-watchdog] nudged {agent} ({by_agent[agent]} over-SLA)",
                              f"Re-nudge (count-only) to {agent} covering {by_agent[agent]} "
                              f"over-SLA message(s) via {mech}; ok={ok}. Triggering: "
                              f"{pr} {v['violation_type']} msg #{mid}, elapsed={elapsed}m.",
                              priority="P3")

            state["msgs"][key] = rec

        # op#11774 #5 INTERIM (page-only): surface a READ-but-PARKED hub P0/P1 that
        # attended_for()'s read==attending rule silences. Independent of the nudge
        # ladder above (a read-parked hub never accrues nudges). Escalate to a
        # SUPERVISOR (never renudge the parked body); deduped via an audit marker;
        # honors the same dry/lease gate as everything else in this block; fail-LOUD
        # (a crash here must not take down the scan).
        # OBSERVE-FIRST (fleet doctrine, per-stage sign): the interim ships INERT —
        # it force-dries (log-only [DRY] READ-PARKED lines, no page) until explicitly
        # ARMED via SLA_READ_PARKED_ENABLED=1 at go-live. The daemon runs this file
        # every 90s (StartInterval), so the code lands safe and pages NOBODY until
        # the env is flipped on an operator/console go-live.
        rp_dry = dry or os.environ.get("SLA_READ_PARKED_ENABLED", "0") != "1"
        try:
            # BACKFILL GUARD: pin the watermark to max(id) the first time the net is
            # ARMED (not before), so the pre-existing backlog is excluded by
            # construction — only stalls crossing the threshold GOING FORWARD page.
            wm = state.get("read_parked_watermark_id")
            if not rp_dry and wm is None:
                wm = _max_message_id(conn)
                state["read_parked_watermark_id"] = wm
                log(f"read-parked backfill-guard ARMED: watermark id={wm} "
                    f"(entire pre-existing hub backlog excluded)")
            parked = read_parked_hub_targets(
                _fetch_read_parked_hub(conn), watermark_id=(wm or 0))
            if parked:
                res = escalate_read_parked(
                    parked, dry=rp_dry,
                    already_paged=lambda mid: _already_escalated_read_parked(conn, mid),
                    escalate_internally=lambda t: escalate_internally(_v_from_target(t), 0),
                    send_page=lambda text: send_page(text, dry),
                    record=lambda mid, outcome: audit(
                        conn, f"[sla-watchdog] READ-PARKED escalated #{mid}",
                        f"read-parked hub escalation ({outcome}) for msg #{mid}", priority="P1"),
                    max_escalations=MAX_READ_PARKED_PER_SCAN,
                )
                for mid, outcome in res:
                    actions.append(f"{'[DRY] ' if rp_dry else ''}READ-PARKED #{mid} -> {outcome}")
                # Observe-first observability: the `actions` list only prints under
                # --dry-run, so surface read-parked outcomes in the LIVE daemon log
                # too — otherwise the [DRY] observe phase is invisible and can't be
                # reviewed before go-live. Log-only (no fire; the gate still force-dries).
                log(f"read-parked [{'DRY/observe' if rp_dry else 'ARMED'}] wm={wm or 0}: "
                    + ", ".join(f"#{m}->{o}" for m, o in res))
        except Exception as e:  # fail LOUD, keep the scan alive (KeepAlive re-runs)
            log(f"read-parked-escalate ERROR: {e!r}")

        if persist:
            save_state(state)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # report
    if dry:
        print(f"\n=== DRY-RUN: {len(violations)} actionable violation(s) "
              f"(window {MAX_VIOLATION_AGE_MIN}m) ===")
        if not actions:
            print("(no actions)")
        for a in actions:
            print("  " + a)
    else:
        log(f"scan complete: {len(violations)} actionable, "
            f"{len([a for a in actions if 'PAGE' in a])} page(s), "
            f"{len([a for a in actions if 'NUDGE' in a])} nudge(s)")
    return 0


# ---------------------------------------------------------------------------
# Self-test — proves backoff + escalation + page-once dedup logic offline.
# ---------------------------------------------------------------------------

def self_test() -> int:
    """Deterministic in-memory exercise of the state machine. No DB, no I/O."""
    import tempfile
    global STATE_FILE
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    with tempfile.TemporaryDirectory() as td:
        STATE_FILE = Path(td) / "state.json"

        # Simulate a persisting fresh P1 (unresponded, elapsed above hard).
        v = {"agent": "cc-scholar", "message_id": 999999, "priority": "P1",
             "from_agent": "cai", "subject": "SIMULATED go-live blocker",
             "violation_type": "unresponded", "elapsed_minutes": 25,
             "threshold_minutes": 20}

        print("Cycle 1 (dry): expect NUDGE, no PAGE (no prior nudge yet)")
        run(dry=True, injected=[v], persist=True)
        s = load_state()
        rec = s["msgs"]["999999:unresponded"]
        check(rec["nudge_count"] == 1, "cycle1 records exactly one nudge attempt")
        check(rec["paged"] is False, "cycle1 does NOT page (needs a prior failed nudge)")

        # Force last_nudge into the past so backoff is irrelevant, keep count.
        rec["last_nudge_ts"] = time.time() - 9999
        s["msgs"]["999999:unresponded"] = rec
        save_state(s)

        print("Cycle 2 (dry): expect PAGE (prior nudge failed to clear it)")
        run(dry=True, injected=[v], persist=True)
        s = load_state()
        rec = s["msgs"]["999999:unresponded"]
        check(rec["paged"] is True, "cycle2 pages once the re-nudge didn't clear it")

        print("Cycle 3 (dry): expect NO second page (page-once dedup)")
        before = json.dumps(s["msgs"]["999999:unresponded"])
        run(dry=True, injected=[v], persist=True)
        s2 = load_state()
        # paged stays true; no new page action possible
        check(s2["msgs"]["999999:unresponded"]["paged"] is True,
              "cycle3 keeps paged=true (never re-pages)")

        print("Backoff: fresh P2 nudged, then immediately re-scanned -> SKIP (backoff)")
        STATE_FILE = Path(td) / "state2.json"
        v2 = {"agent": "cc-scholar", "message_id": 888888, "priority": "P2",
              "from_agent": "cai", "subject": "SIM P2", "violation_type": "unread",
              "elapsed_minutes": 300, "threshold_minutes": 240}
        run(dry=True, injected=[v2], persist=True)  # nudge
        c1 = load_state()["msgs"]["888888:unread"]["nudge_count"]
        run(dry=True, injected=[v2], persist=True)  # backoff -> no new nudge
        c2 = load_state()["msgs"]["888888:unread"]["nudge_count"]
        check(c1 == 1 and c2 == 1, "P2 re-nudge suppressed by backoff on immediate re-scan")

        print("P2/P3 never escalate: a very-old-but-in-window P2 past hard-equivalent")
        # even with many prior nudges, P2 must never page
        STATE_FILE = Path(td) / "state3.json"
        s3 = {"msgs": {"777777:unread": {"nudge_count": 5, "last_nudge_ts": 0,
                                         "paged": False, "paged_ts": 0,
                                         "last_seen_ts": time.time()}}}
        save_state(s3)
        v3 = {"agent": "cc-scholar", "message_id": 777777, "priority": "P2",
              "from_agent": "cai", "subject": "SIM P2 persistent",
              "violation_type": "unread", "elapsed_minutes": 999,
              "threshold_minutes": 240}
        run(dry=True, injected=[v3], persist=True)
        check(load_state()["msgs"]["777777:unread"]["paged"] is False,
              "P2 never pages regardless of age / nudge count")

    print()
    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} failure(s))")
        return 1
    print("SELF-TEST PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Priority-SLA watchdog (action + escalation).")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be nudged/paged; take NO action")
    ap.add_argument("--self-test", action="store_true",
                    help="run offline backoff/dedup/escalation logic checks")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return run(dry=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
