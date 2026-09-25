"""hub_self_recovery.py — gzb-LOCAL wedge-recovery decision core (op#42896 follow-on).

THE GAP THIS CLOSES: PR #145 made hub_reach.py's gzb remedy honestly "escalate to a
human" — the old wingmen-core relay it used to nudge a WEDGED-but-ALIVE hub over is
decommissioned, and nothing replaced it. A live `orch` tmux session on gzb with a
staged/ghost composer today has ZERO automated recovery. `wingmen-orch-hub.service` /
orch_supervisor.sh already restart the hub on process DEATH — this is specifically the
wedged-but-alive case, which a process restart cannot fix (the process never died).

PROVENANCE: proposed reports/hub-self-wedge-recovery-proposal.md (2026-09-25),
re-framed per orch-console's #43134 (actor = the hub's own host supervisor, gated on
`orch_lease.holder_host=='gzbai'` — NEVER `fleet_health_lease`/cc-fleet-health, per
CAI-RESP-501/681 keeping singleton-affecting action away from the SRE). Approved
with 6 conditions by cai (CAI-RESP-1439, bus #43148). The hub itself CONSENTED
(bus #43157). orch-console authorized building it, OBSERVE-FIRST, with 4 BINDING
design inputs plus a DB kill-flag amendment (bus #43161):

  (a) CRITICAL — detection gates on PENDING WORK, never bare idleness. The hub sits
      idle at the prompt for hours BY DESIGN (P2 rows don't wake it). "Wedged" means:
      pending work exists (an unprocessed operator_log row on the hub's channels, OR
      an unread P1+requires_response bus row addressed to cc-orchestrator, older than
      a threshold) AND the hub is not draining it (not busy) AND the composer shows
      staged/ghost text (non-empty). Idle with NOTHING pending is healthy — never act.
      See `wedge_detected()`; exercised against exactly the 2026-09-25 case this
      condition names (22.5h idle, only P2 unread -> no action) in the test suite.
  (b) Refuse on ANY menu/picker — the model picker, a trust prompt, the resume
      picker, a permission dialog. `pane_is_menu` (composer_capture.sh) does not
      necessarily match every one of those on its generic nav-footer regex, so the
      driver script composes it with the two picker-specific predicates
      (`trust_prompt_present`, `resume_menu_present`) that already exist for exactly
      this in composer_capture.sh, per orch-console's explicit instruction to extend
      the fixtures rather than assume the generic check covers them.
  (c) Never act mid-turn or mid-autocompact — `pane_is_busy` (the "esc to interrupt" /
      background-agent / thinking-spinner marker, shared with every other watchdog in
      this fleet) gates this, unchanged, reused as-is.
  (d) The resubmit payload is a FIXED, generic, hardcoded string — never operator
      words, never authorization content, never templated from row content (R1/R2
      doctrine). See `FIXED_RESUBMIT_PAYLOAD`.
  Plus: the kill switch is (a flag FILE on gzb) AND (this module's DB settings row)
  — EITHER being off disables the ACT step; see `kill_switch_enabled()`. And: every
  detection carries the SPECIFIC triggering row id, so a repeated detection on the
  SAME underlying stuck item is capped by a per-row LIFETIME ceiling, not just an
  hourly rate limit (see `row_lifetime_count` / `_ROW_LIFETIME_CEILING`).

OBSERVE-FIRST (bus #43161): `evaluate(..., mode="observe")` — the default, and the
only mode the deployed systemd unit passes today — NEVER sets `act=True`; a genuine
detection is logged as `action="would-nudge"` only. Graduating to `mode="act"` (which
delegates the actual nudge to `scripts/lane_nudge.sh`, reusing its verified-submit +
menu-refuse + ghost-vs-real guards unchanged — this module never types into the pane
itself) is orch-console's call, made by changing the systemd unit's ExecStart args
after the observation window (>=72h AND >=3 genuine detections, per bus #43161),
never a default this code silently flips itself.

This module holds the PURE decision core (independently unit-testable, no DB, no
tmux) plus the thin DB read/write/CLI layer that wires it to the real substrate. The
pane-facing checks (busy/menu/composer state) are computed by the bash driver
(scripts/hub_self_recovery.sh, which sources composer_capture.sh — the fleet's ONE
tested ghost-vs-real/busy/menu logic) and passed in as booleans; this module never
re-implements pane parsing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ORCH_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
from scripts.lib import hub_reach  # noqa: E402

HUB_AGENT = "cc-orchestrator"
HUB_TMUX = "orch"
SELF_HOST = "gzbai"

# FIXED, hardcoded, never templated — condition (d). If the ACT step ever fires,
# this exact literal is what gets typed; nothing derived from operator/row content.
FIXED_RESUBMIT_PAYLOAD = "reconcile your inbox"

# How stale an unread P1+requires_response bus row to cc-orchestrator must be before
# it counts as "pending work" for the wedge test (part of condition (a)).
_BUS_PENDING_AGE_S = int(os.environ.get("HSR_BUS_PENDING_AGE_S", "900"))  # 15min
_RATE_LIMIT_PER_HOUR = int(os.environ.get("HSR_RATE_LIMIT_PER_HOUR", "3"))
_ROW_LIFETIME_CEILING = int(os.environ.get("HSR_ROW_LIFETIME_CEILING", "5"))
_KILL_FILE = Path(os.environ.get("HSR_KILL_FILE", os.path.expanduser("~/.hub_self_recovery_disabled")))


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _connect():
    import psycopg  # local import: keeps the pure functions below importable with no driver installed
    return psycopg.connect(_dsn(), connect_timeout=10)


# --------------------------------------------------------------------------- #
# PURE decision core — no DB, no tmux, no clock side effects. Unit-tested offline.
# --------------------------------------------------------------------------- #

def lease_is_self(holder_host) -> bool:
    """PURE. True only when `orch_lease.holder_host` POSITIVELY resolves to THIS
    host (gzbai) — reuses hub_reach's fail-closed host-match (PR #145) rather than
    a new comparison, so an unknown/unset/foreign holder never acts (never a body
    acting on a lease it does not currently hold — Nazim's ORCH-TOPOLOGY-001 shape,
    applied here to the hub's OWN self-recovery rather than the 5 console pens)."""
    return hub_reach.is_reach_host_current(SELF_HOST, holder_host)


def kill_switch_enabled(*, file_present: bool, db_enabled) -> bool:
    """PURE. orch-console's amendment: the FILE (default-absent=enabled) AND the DB
    settings row (default-true=enabled) must BOTH allow — either one saying "off"
    disables the ACT step. db_enabled=None (row/table missing, unreadable, a
    permission error, or any other exception) fails CLOSED to disabled (CAI-RESP-
    1439 cond 5 + FULL-tier audit #43169 finding 2): an unverifiable DB flag must
    never be treated as "the switch says go" — the migration is applied BEFORE this
    is ever enabled in production anyway, so "pre-migration" is not a live reason
    to fail open. Only an explicit, positively-read db_enabled=True allows."""
    if file_present:
        return False
    return db_enabled is True


def pending_work_verdict(*, operator_unprocessed: list, stale_p1_bus_rows: list) -> dict:
    """PURE. `operator_unprocessed` — rows from operator_log.unprocessed(), scoped to
    the hub's own channels. `stale_p1_bus_rows` — (id, created_at) tuples: unread,
    requires_response, priority='P1' agent_messages rows addressed to cc-orchestrator,
    older than `_BUS_PENDING_AGE_S`. Returns {'pending', 'kind', 'row_id'}. When both
    are present the operator row wins the triggering-row-id tie-break (arbitrary but
    deterministic — the lifetime ceiling only needs ONE row id per detection, not
    every candidate)."""
    if operator_unprocessed:
        return {"pending": True, "kind": "operator_log", "row_id": operator_unprocessed[0][0]}
    if stale_p1_bus_rows:
        return {"pending": True, "kind": "bus_p1", "row_id": stale_p1_bus_rows[0][0]}
    return {"pending": False, "kind": None, "row_id": None}


def wedge_detected(*, pending: dict, busy: bool, menu: bool, composer_empty: bool) -> "tuple[bool, str]":
    """PURE. The exact AND per orch-console's #43161 binding condition (a), with (b)
    and (c)'s hard refuses checked FIRST and unconditionally (a menu or a busy pane
    refuses even with pending work sitting there — these are never overridden by
    anything else). Idle + nothing pending is healthy, never a wedge — this is the
    predicate exercised against the named 2026-09-25 case (22.5h idle, only P2
    unread -> pending=False -> no action) in the test suite."""
    if menu:
        return False, "refused: pane parked in a menu/picker"
    if busy:
        return False, "refused: pane busy (mid-turn/mid-autocompact)"
    if not pending["pending"]:
        return False, "no pending work — healthy idle, never act"
    if composer_empty:
        return False, "pending work but the composer is empty — not the wedge signature this covers"
    return True, f"WEDGED: pending={pending['kind']}#{pending['row_id']}, composer holds staged/ghost text"


# --------------------------------------------------------------------------- #
# DB layer — thin, mirrors the fleet's existing seams (own short-lived
# connections, fail loud on a genuine error, never silently reinterpret one).
# --------------------------------------------------------------------------- #

def fetch_operator_unprocessed(limit: int = 5) -> list:
    """The hub's own unprocessed operator_log rows. Requires ORCH_BODY_ROLE='hub'
    (checked by the caller BEFORE this is reached — see `evaluate()`) so this reuses
    operator_log's real hub channel-scope SQL rather than a second, driftable copy."""
    from nervous_system import operator_log
    return operator_log.unprocessed(limit=limit)


def fetch_kill_switch_db_enabled(conn, cur):
    """The DB half of the kill switch. None (row/table missing, unreadable, or any
    other exception) -> caller's kill_switch_enabled() fails CLOSED to disabled
    (audit #43169 finding 2); a real False always wins too."""
    try:
        cur.execute("SELECT enabled FROM hub_self_recovery_settings LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 — pre-migration / table missing
        conn.rollback()
        return None


def fetch_stale_p1_bus_rows(cur, *, max_age_s: int, limit: int = 5) -> list:
    cur.execute(
        "SELECT id, created_at FROM agent_messages "
        "WHERE to_agent=%s AND priority='P1' AND requires_response=true AND read_at IS NULL "
        "AND created_at < now() - make_interval(secs => %s) "
        "ORDER BY id ASC LIMIT %s",
        (HUB_AGENT, max_age_s, limit),
    )
    return cur.fetchall()


def recent_action_count(cur, *, since) -> int:
    cur.execute(
        "SELECT count(*) FROM hub_self_recovery_log "
        "WHERE action IN ('nudged', 'would-nudge') AND detected_at >= %s",
        (since,),
    )
    return cur.fetchone()[0]


def row_lifetime_count(cur, *, triggering_row_id, pending_kind) -> int:
    if triggering_row_id is None:
        return 0
    cur.execute(
        "SELECT count(*) FROM hub_self_recovery_log "
        "WHERE triggering_row_id=%s AND pending_kind=%s",
        (triggering_row_id, pending_kind),
    )
    return cur.fetchone()[0]


def rate_limit_state_logged(cur, *, since) -> bool:
    """Audit #43169 finding 3 (flood): a 'rate-limited' row already logged inside
    the CURRENT hourly window means the state is already durably recorded — log it
    (and page on it) at most ONCE per window, not once per 2-minute tick for as
    long as the underlying stuck condition persists."""
    cur.execute(
        "SELECT EXISTS(SELECT 1 FROM hub_self_recovery_log WHERE action='rate-limited' AND detected_at >= %s)",
        (since,),
    )
    return cur.fetchone()[0]


def ceiling_state_logged(cur, *, triggering_row_id, pending_kind) -> bool:
    """Audit #43169 finding 3 (flood): 'ceiling-reached' is a LIFETIME verdict on
    this specific (triggering_row_id, pending_kind) — once logged, it stays true
    forever for that row (row_lifetime_count only ever grows), so without this
    check every subsequent 2-minute tick would re-log 'ceiling-reached' and page
    again, forever. Log it (and page on it) ONCE per (row, state)."""
    if triggering_row_id is None:
        return False
    cur.execute(
        "SELECT EXISTS(SELECT 1 FROM hub_self_recovery_log WHERE action='ceiling-reached' "
        "AND triggering_row_id=%s AND pending_kind=%s)",
        (triggering_row_id, pending_kind),
    )
    return cur.fetchone()[0]


def stamp_tick(cur, *, saw_session: bool, reason: str) -> None:
    """Audit #43169 finding 6 (silent no-op indistinguishable from healthy): one
    UPDATE per tick on the singleton settings row, regardless of outcome. Without
    this, a script that never even reaches its own logic (e.g. the tmux session
    belongs to a different OS user than the systemd unit's User=) exits 0 on every
    tick and leaves ZERO trace — 72h of total silence would look identical to 72h
    of genuinely healthy observation. Called on every real invocation (this
    module's evaluate() AND the bash driver's no-session branch, via
    record_no_session()) so absence of this stamp updating is itself the signal
    that the tick never ran at all."""
    cur.execute(
        "UPDATE hub_self_recovery_settings SET last_tick_at=now(), "
        "last_tick_saw_session=%s, last_tick_reason=%s WHERE id=true",
        (saw_session, reason),
    )


def log_event(cur, *, mode: str, action: str, pending_kind, triggering_row_id, detail: str) -> int:
    cur.execute(
        "INSERT INTO hub_self_recovery_log (mode, action, pending_kind, triggering_row_id, detail) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (mode, action, pending_kind, triggering_row_id, detail),
    )
    return cur.fetchone()[0]


def _page_observation(cur, *, action: str, detail: str) -> None:
    """One low-priority, non-flooding bus row per genuine, newly-logged detection
    (in ANY mode) — 'silence is never the outcome of an action' per the original
    proposal, and the observe-first evidence needs a human to actually SEE it land,
    not just pile silently into a table someone has to remember to query. P3/update
    (not a page): this is observation data, not an emergency, and observe-mode by
    construction never touches the pane — flooding P1 alerts here would just be
    noise. Raises on failure — the caller (evaluate()) is responsible for isolating
    this from the already-committed audit row (audit #43169 finding 4: this used to
    share a transaction with the audit INSERT, so a failed page here aborted the
    whole transaction and silently discarded the audit row too)."""
    cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (HUB_AGENT,))
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body,requires_response) "
        "VALUES (%s,'orch-console','update','P3',%s,%s,false)",
        (HUB_AGENT, f"hub-self-recovery: {action}", detail),
    )


# --------------------------------------------------------------------------- #
# The wired decision — combines the pure core with real reads, one DB round-trip,
# ONE log row (only on a genuine, non-refused detection — a healthy-idle tick
# writes nothing, so a 2-minute cadence never floods the audit table).
# --------------------------------------------------------------------------- #

def evaluate(*, busy: bool, menu: bool, composer_empty: bool, mode: str) -> dict:
    """Full decision + side effects. Returns
    {'act': bool, 'reason': str, 'payload': str}. `act` is True ONLY when
    mode=='act' AND a genuine detection AND neither the hourly rate limit nor the
    per-row lifetime ceiling is hit — the caller (the bash driver) invokes
    lane_nudge.sh IFF `act` is True; this function itself never touches a pane."""
    result = {"act": False, "reason": "", "payload": FIXED_RESUBMIT_PAYLOAD}

    if os.environ.get("ORCH_BODY_ROLE", "").strip().lower() != "hub":
        result["reason"] = ("refused: ORCH_BODY_ROLE is not 'hub' — this recovery script only ever "
                             "runs as the hub body (it reads the hub's OWN operator-log scope)")
        return result

    file_present = _KILL_FILE.exists()

    with _connect() as conn, conn.cursor() as cur:
        try:
            holder_host = hub_reach.read_holder_host(conn)
            if not lease_is_self(holder_host):
                result["reason"] = (f"refused: orch_lease.holder_host={holder_host!r} does not resolve to "
                                     f"this host ({SELF_HOST}) — acts on itself only, never a body it doesn't "
                                     f"currently hold the lease for")
                return result

            db_enabled = fetch_kill_switch_db_enabled(conn, cur)
            if not kill_switch_enabled(file_present=file_present, db_enabled=db_enabled):
                result["reason"] = f"refused: kill switch OFF (file_present={file_present}, db_enabled={db_enabled})"
                return result

            op_rows = fetch_operator_unprocessed()
            bus_rows = fetch_stale_p1_bus_rows(cur, max_age_s=_BUS_PENDING_AGE_S)
            pending = pending_work_verdict(operator_unprocessed=op_rows, stale_p1_bus_rows=bus_rows)

            detected, reason = wedge_detected(pending=pending, busy=busy, menu=menu, composer_empty=composer_empty)
            result["reason"] = reason
            if not detected:
                return result  # healthy or hard-refused — nothing logged, no flood on a healthy hub

            since = datetime.now(timezone.utc) - timedelta(hours=1)
            recent = recent_action_count(cur, since=since)
            lifetime = row_lifetime_count(cur, triggering_row_id=pending["row_id"], pending_kind=pending["kind"])

            # Audit #43169 finding 3 (flood): a 'ceiling-reached' or 'rate-limited'
            # verdict is a STATE, not a fresh event — once it's been durably logged
            # for this window (rate limit) or this row (ceiling, lifetime-scoped), a
            # 2-minute tick must keep refusing to act WITHOUT re-logging/re-paging
            # forever. already_logged short-circuits before the INSERT below.
            already_logged = False
            if lifetime >= _ROW_LIFETIME_CEILING:
                action = "ceiling-reached"
                result["reason"] = (f"WEDGE detected but the lifetime ceiling ({_ROW_LIFETIME_CEILING}) is "
                                     f"reached on {pending['kind']}#{pending['row_id']} — escalate to a human, "
                                     f"no further auto-action on this row")
                already_logged = ceiling_state_logged(cur, triggering_row_id=pending["row_id"],
                                                       pending_kind=pending["kind"])
            elif recent >= _RATE_LIMIT_PER_HOUR:
                action = "rate-limited"
                result["reason"] = f"WEDGE detected but the hourly rate limit ({_RATE_LIMIT_PER_HOUR}/hr) is hit — standing off this tick"
                already_logged = rate_limit_state_logged(cur, since=since)
            elif mode == "act":
                action = "nudged"
                result["act"] = True
            else:
                action = "would-nudge"

            if already_logged:
                return result

            log_event(cur, mode=mode, action=action, pending_kind=pending["kind"],
                       triggering_row_id=pending["row_id"], detail=result["reason"])
            conn.commit()  # audit #43169 finding 4: the audit row is durable BEFORE
            # a page is even attempted — a failed page must never be able to roll
            # back or otherwise destroy a detection that already happened.
            try:
                _page_observation(cur, action=action, detail=result["reason"])
                conn.commit()
            except Exception:  # noqa: BLE001 — the audit row above is already committed;
                # a paging failure (e.g. agent_messages RLS/connectivity) is best-effort
                # and must never be allowed to look like the detection itself failed.
                conn.rollback()
            return result
        finally:
            # Audit #43169 finding 6: exactly one liveness stamp per real tick that
            # reached the DB, on EVERY path out of this block (healthy, refused,
            # rate-limited, ceiling, would-nudge, nudged) — so a tick that silently
            # stops reaching even this far (e.g. this whole try block starts raising)
            # is the one case that does NOT get a fresh stamp, which is exactly the
            # detectable signal a stale last_tick_at is for.
            stamp_tick(cur, saw_session=True, reason=result["reason"])
            conn.commit()


def record_no_session() -> None:
    """Audit #43169 finding 6, the no-session half: the bash driver's no-tmux-
    session branch exits before any pane-state read is even possible, so
    evaluate() itself is never reached on that tick — without this, that branch
    would leave zero trace in `last_tick_at`, indistinguishable from a healthy
    tick that simply never ran. Same guards as evaluate() (hub role + self lease)
    so a foreign body or a non-lease-holding host never stamps the hub's own
    liveness row on its behalf."""
    if os.environ.get("ORCH_BODY_ROLE", "").strip().lower() != "hub":
        return
    with _connect() as conn, conn.cursor() as cur:
        holder_host = hub_reach.read_holder_host(conn)
        if not lease_is_self(holder_host):
            return
        stamp_tick(cur, saw_session=False, reason="no tmux session found")
        conn.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--busy", type=int, choices=[0, 1])
    ap.add_argument("--menu", type=int, choices=[0, 1])
    ap.add_argument("--composer-empty", type=int, choices=[0, 1])
    ap.add_argument("--mode", choices=["observe", "act"], default="observe")
    ap.add_argument("--no-session", action="store_true",
                     help="the driver found no live tmux session this tick — record "
                          "a liveness stamp only, never evaluate a wedge")
    a = ap.parse_args(argv)
    if a.no_session:
        record_no_session()
        print(json.dumps({"act": False, "reason": "no tmux session found", "payload": None}))
        return 0
    if a.busy is None or a.menu is None or a.composer_empty is None:
        ap.error("--busy/--menu/--composer-empty are required unless --no-session")
    result = evaluate(busy=bool(a.busy), menu=bool(a.menu),
                       composer_empty=bool(a.composer_empty), mode=a.mode)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
