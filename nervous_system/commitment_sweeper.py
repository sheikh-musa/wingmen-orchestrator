"""commitment_sweeper — fire durable commitments whose clock has come due.

CAI-RESP-1029 build 1/2 (owner orch-console). The other half is migration 051
(`held_commitments`), which is STORAGE ONLY and deliberately fires nothing.

WHY THIS EXISTS: cc-irsyad-coord held a client deadline as a background alarm inside its own
live process. It recycled. The pre-cleared text survived; the trigger did not. cai's ruling:
that is a durability defect, not a headcount one — persist the clock to the substrate and let
ANY body or trigger re-hydrate it.

⚠ CAI-RESP-1029 BINDS: this does not count as existing until it has been BUILT + LOADED +
  **EXERCISED** — observed to actually fire. `scripts/residency_sweep.py` was authored,
  committed, and had its plist written on 2026-07-02, and has not run since. Six weeks of a
  standing residency self-audit not running, because the last step had no owner. A sweeper
  that has never fired is that failure with better formatting.

THREE THINGS THIS REFUSES TO DO, each bought with a specific incident:

  1. **It never marks a commitment DISCHARGED.** Firing is a machine event; discharge is a claim
     that a human actually got the thing. Only a body that believes the promise was kept writes
     `discharged_by`, and it must name itself. Auto-discharging on fire would let the table report
     a promise kept because a trigger shouted into a pane that may not exist — the same
     substitution as reading a launchd exit status as a run record.

  2. **It never addresses a body that does not exist.** `sla-watchdog` has no `agent_status` row;
     all 12 messages ever sent to it came from console bodies and 11 sat unread for days, each
     unread reply becoming the next false P1. A message to an address nothing owns cannot be LATE,
     only MISADDRESSED. So: if a commitment's owner has no live `agent_status` row, this escalates
     to the console instead of firing into the void, and says so.

  3. **It is idempotent and it does not nag.** A due commitment fires ONCE (pending -> fired).
     Re-running the sweeper does not re-fire it. Escalation of a fired-but-undischarged commitment
     is rate-limited by the escalation interval, because a watchdog whose output is noise gets
     muted, and a muted watchdog is an absent one.

Usage:
  python -m nervous_system.commitment_sweeper            # dry-run: report, change nothing
  python -m nervous_system.commitment_sweeper --fire     # actually fire + notify
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import sys
import uuid

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from nervous_system import singleton_liveness as _sl  # hub_alive_evidence (audit #1A)

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A3's defect, and the reason this line exists: `a3_isolation_check.py` read os.environ only and
# never called load_dotenv. It worked by hand — our shells are children of a claude process booted
# with .env sourced — and failed the moment launchd ran it, because launchd inherits nothing.
# Works-interactively-fails-under-the-scheduler is the signature failure of this whole class, so
# load from the repo root explicitly rather than trusting the caller's environment.
load_dotenv(ROOT / ".env")

# How long a FIRED-but-undischarged commitment may sit before the console is told. Not a client
# SLA — just the interval at which an unanswered promise stops being in-flight and starts being
# dropped.
ESCALATE_AFTER = _dt.timedelta(minutes=30)

# How stale a heartbeat may be before a body stops counting as able to drain an inbox. Lanes
# heartbeat every ~5 min, so this is generous — the point is to exclude the DEAD, not to police
# the slow. See _live_agents for why row-existence alone is not liveness.
LIVENESS_MAX_STALE_MIN = 20

CONSOLE = "orch-console"

# bus #44547: _notify() used to hardcode from_agent='orch-console' on every INSERT, so a
# DUE/FIRED commitment row misattributed itself as having come from the console (e.g.
# #44511 arrived as orch-console -> orch-console). Dedicated identity, registered by
# migration 076 (agent_messages.from_agent FKs to agents.id -- the identity must exist
# BEFORE the first row posts under it, same lesson as ingest-watchdog/#44035).
FROM_AGENT = "commitment-sweeper"


def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _live_agents(cur) -> set[str]:
    """Agent ids that will ACTUALLY drain an inbox.

    The sla-watchdog lesson in one query: an id that owns no inbox is not
    delivered-and-waiting, it is lost — a message to such an address cannot be late, only
    misaddressed.

    ⚠ ROW-EXISTS IS NOT LIVENESS, and my first version of this got it wrong. A culled lane
      KEEPS its agent_status row; the launcher's exit trap flips it to 'offline'. Checked an
      hour after standing three lanes down: cc-irsyad-receipt-1 offline/34m, cc-irsyad-2
      offline/6m, cc-irsyad-3 offline/5m — all with rows, all with no tmux session. So an
      existence test would have happily fired a commitment into three dead bodies. Under
      CAI-RESP-1029 elasticity, lanes are culled routinely and this only gets worse over time.
      Liveness therefore needs all three: the row exists, it is not 'offline', and its
      heartbeat is recent.
    """
    cur.execute(
        """SELECT agent_id FROM agent_status
            WHERE COALESCE(status,'') <> 'offline'
              AND last_heartbeat IS NOT NULL
              AND last_heartbeat > now() - interval '%s minutes'""" % LIVENESS_MAX_STALE_MIN)
    live = {r["agent_id"] for r in cur.fetchall()}
    # Sub-tagged bodies (cc-irsyad-coord-1) drain mail addressed to their base family
    # (cc-irsyad-coord). Roll the base up from LIVE sub-tags only — rolling up from a dead
    # sub-tag would resurrect exactly the address this function exists to rule out.
    for aid in list(live):
        parts = aid.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            live.add(parts[0])
    # The cross-host hub renews orch_lease from its OWN host, not the Mini hb loop, so it
    # is absent from the heartbeat-window query above even when fully alive (audit #1). A
    # FRESH lease is authoritative liveness — without this belt a commitment addressed to
    # cc-orchestrator would read as misaddressed and misroute. hub_alive_evidence() is the
    # single shared signal (Nazim 37658 scope (a)); only a definitively-expired lease keeps
    # the hub out.
    if _sl.hub_alive_evidence():
        live.add(_sl.HUB_AGENT)
    return live


def _notify(cur, to_agent: str, subject: str, body: str, priority: str = "P1",
            requires_response: bool = True) -> int:
    """requires_response defaults True (bus #44527): a DUE/FIRED commitment is always an
    action item, never advisory-only, so it must clear the hub's wake floor (P0/P1 AND
    requires_response) whenever it's addressed there — #44508 landed P1 + rr=FALSE and sat
    unseen for ~14h because that combination falls under the floor."""
    # RPC on the EXISTING cursor/connection (backlog#124, bus #74939), not
    # bus_send.py's send() -- send() opens its OWN separate connection and would
    # commit independently of the held_commitments UPDATE that follows each call,
    # and of every other commitment in this sweep's batch (sweep() commits the
    # whole due-list together, or rolls all of it back on dry-run/failure).
    cur.execute(
        """SELECT * FROM post_agent_message(%s, %s, 'update', %s, %s, %s, %s,
                                             %s, %s, %s, %s)""",
        (FROM_AGENT, to_agent, subject[:300], body, priority, requires_response,
         str(uuid.uuid4()), None, False, None),
    )
    return cur.fetchone()["id"]


# hc#581 (console #78229/#78250): every open operator ask with a committed_date gets ONE
# held_commitment owned by the console, so a lane that misses its date pages the console at
# the deadline through the fire path below. No new daemon: hydrated at the top of each sweep.
ASK_SOURCE_PREFIX = "operator_asks:"
ASK_CHANNEL_TAG = "operator-asks"
# committed_date stored at exactly 00:00:00Z is a DATE, not a time (164 rows, some earlier
# than their own ask). Console ruling #78250: it means the END of that day in Musa's
# timezone. Any other time is an exact deadline.
MUSA_TZ = "Asia/Dubai"


def ask_deadline(committed: _dt.datetime) -> _dt.datetime:
    """committed_date -> the moment the console should be paged (UTC)."""
    from zoneinfo import ZoneInfo
    u = committed.astimezone(_dt.timezone.utc)
    if (u.hour, u.minute, u.second, u.microsecond) != (0, 0, 0, 0):
        return u
    end = _dt.datetime.combine(u.date() + _dt.timedelta(days=1), _dt.time(0),
                               tzinfo=ZoneInfo(MUSA_TZ))
    return end.astimezone(_dt.timezone.utc)


def plan_ask_commitments(asks: list[dict], existing: list[dict]) -> list[tuple]:
    """PURE: what to change so each ask has exactly the commitment it should.

    Returns ('insert', ask, deadline) | ('reschedule', row, deadline) | ('cancel', row, reason).
    - open ask, no active row (and no earlier row at/after this deadline) -> insert
    - open ask, pending row with a different deadline                    -> reschedule
    - open ask, FIRED row but the lane re-committed to a LATER date       -> cancel + insert
    - ask closed (or no longer triaged 'ask') with an active row          -> cancel
    Closing the ask is itself the attributable record that the promise resolved, so the
    commitment is CANCELLED with that reason — never DISCHARGED (rule 1 above) — which also
    stops the fired-row escalation re-paging the console every ESCALATE_AFTER."""
    by_ref: dict[str, list[dict]] = {}
    for r in existing:
        if (r.get("source_ref") or "").startswith(ASK_SOURCE_PREFIX):
            by_ref.setdefault(r["source_ref"], []).append(r)
    out = []
    for a in asks:
        rows = by_ref.get(f"{ASK_SOURCE_PREFIX}{a['id']}", [])
        active = [r for r in rows if r["status"] in ("pending", "fired")]
        is_open = a.get("closed_at") is None and a.get("triage_state") == "ask"
        if not is_open:
            why = (f"ask #{a['id']} closed ({a.get('closed_reason') or 'no reason given'})"
                   if a.get("closed_at") else
                   f"ask #{a['id']} re-triaged to {a.get('triage_state')}")
            out.extend(("cancel", r, why) for r in active)
            continue
        if a.get("committed_date") is None:
            continue
        deadline = ask_deadline(a["committed_date"])
        if active:
            for r in active:
                if r["status"] == "pending" and r["due_at"] != deadline:
                    out.append(("reschedule", r, deadline))
                elif r["status"] == "fired" and deadline > r["due_at"]:
                    out.append(("cancel", r, f"re-committed to {deadline:%Y-%m-%d %H:%M}Z"))
                    out.append(("insert", a, deadline))
            continue
        if not rows or max(r["due_at"] for r in rows) < deadline:
            out.append(("insert", a, deadline))
    return out


def hydrate_ask_commitments(conn, fire: bool) -> int:
    """Impure: apply plan_ask_commitments in its OWN tx (rolled back on dry-run), before the
    core sweep, so a hydrate failure can never stop existing commitments firing. Returns the
    number of changes."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT oa.id, oa.source_msg_id AS op, oa.delegated_to, oa.committed_date,
                      oa.closed_at, oa.closed_reason, oa.triage_state,
                      left(coalesce(oa.triage_summary, oa.ask), 200) AS summary
                 FROM operator_asks oa
                WHERE (oa.committed_date IS NOT NULL AND oa.closed_at IS NULL
                       AND oa.triage_state = 'ask')
                   OR EXISTS (SELECT 1 FROM held_commitments h
                               WHERE h.source_ref = %s || oa.id
                                 AND h.status IN ('pending','fired'))""",
            (ASK_SOURCE_PREFIX,))
        asks = cur.fetchall()
        cur.execute(
            "SELECT id, source_ref, status, due_at FROM held_commitments "
            "WHERE source_ref LIKE %s", (ASK_SOURCE_PREFIX + "%",))
        existing = cur.fetchall()
        plan = plan_ask_commitments(asks, existing)
        for kind, obj, arg in plan:
            if kind == "insert":
                lane = obj.get("delegated_to") or "(no lane delegated)"
                title = (f"op#{obj.get('op')} ask #{obj['id']} past its committed date — "
                         f"lane {lane}")
                # held_commitments_payload_valid_json: payload must parse as JSON
                payload = json.dumps({
                    "op": obj.get("op"), "ask_id": obj["id"], "lane": lane,
                    "ask": obj.get("summary") or "",
                    "committed_for": f"{arg:%Y-%m-%d %H:%M}Z",
                    "what_to_do": ("Chase the lane or re-commit the date with Musa. This "
                                   "commitment cancels itself when the ask is closed "
                                   "(scripts/asks_close.py) — close the ask, don't discharge "
                                   "this row by hand."),
                })
                cur.execute(
                    """INSERT INTO held_commitments
                         (owner_agent, title, payload, due_at, status, source_ref,
                          channel_tag, created_by)
                       VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s)""",
                    (CONSOLE, title[:300], payload, arg, f"{ASK_SOURCE_PREFIX}{obj['id']}",
                     ASK_CHANNEL_TAG, FROM_AGENT))
                print(f"{'' if fire else '[dry-run] '}ASK-COMMIT insert ask #{obj['id']} "
                      f"due {arg:%Y-%m-%d %H:%M}Z")
            elif kind == "reschedule":
                cur.execute(
                    "UPDATE held_commitments SET due_at=%s, updated_at=now() "
                    "WHERE id=%s AND status='pending'", (arg, obj["id"]))
                print(f"{'' if fire else '[dry-run] '}ASK-COMMIT reschedule #{obj['id']} "
                      f"-> {arg:%Y-%m-%d %H:%M}Z")
            else:
                cur.execute(
                    "UPDATE held_commitments SET status='cancelled', cancelled_at=now(), "
                    "cancel_reason=%s, updated_at=now() "
                    "WHERE id=%s AND status IN ('pending','fired')", (arg, obj["id"]))
                print(f"{'' if fire else '[dry-run] '}ASK-COMMIT cancel #{obj['id']}: {arg}")
    if fire:
        conn.commit()
    else:
        conn.rollback()
    return len(plan)


def sweep(fire: bool) -> int:
    dsn = _dsn()
    if not dsn:
        # Fail LOUD, never no-op silently — D6, and the behaviour A3 got right on its first run.
        print("commitment_sweeper: MISSING DSN — cannot run; refusing to no-op silently",
              file=sys.stderr)
        return 2

    hydrate_failed = False
    try:
        with psycopg.connect(dsn, connect_timeout=20, row_factory=dict_row) as hconn:
            hydrate_ask_commitments(hconn, fire)
    except Exception as e:  # noqa: BLE001 — the core sweep must still run; exit non-zero below
        hydrate_failed = True
        print(f"commitment_sweeper: ASK-COMMIT HYDRATE FAILED: {type(e).__name__}: {e}",
              file=sys.stderr)

    rc = _sweep_due(dsn, fire)
    return rc if rc else (3 if hydrate_failed else 0)


def _sweep_due(dsn: str, fire: bool) -> int:
    fired = escalated = skipped_unowned = 0

    with psycopg.connect(dsn, connect_timeout=20, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            live = _live_agents(cur)

            cur.execute(
                """SELECT id, owner_agent, title, payload, due_at, status, source_ref,
                          channel_tag, fired_at
                     FROM held_commitments
                    WHERE status IN ('pending','fired')
                      AND due_at <= now()
                    ORDER BY due_at""")
            due = cur.fetchall()

            for c in due:
                owner = c["owner_agent"]
                owned = owner in live
                late = _dt.datetime.now(_dt.timezone.utc) - c["due_at"]

                if c["status"] == "pending":
                    target = owner if owned else CONSOLE
                    if not owned:
                        skipped_unowned += 1
                    subject = (f"COMMITMENT DUE #{c['id']}: {c['title']}"
                               if owned else
                               f"COMMITMENT DUE #{c['id']} but owner '{owner}' OWNS NO INBOX: "
                               f"{c['title']}")
                    body = (
                        f"A commitment you hold came due at {c['due_at']:%Y-%m-%d %H:%M:%S}Z "
                        f"(late by {late}).\n\n"
                        f"  title:      {c['title']}\n"
                        f"  owner:      {owner}\n"
                        f"  source:     {c['source_ref'] or '(none recorded)'}\n"
                        f"  channel:    {c['channel_tag'] or '(none recorded)'}\n\n"
                        f"PAYLOAD (what was promised / pre-cleared):\n{c['payload'] or '(none)'}\n\n"
                        "⚠ THIS IS A FIRE, NOT A DISCHARGE. The commitment is still OPEN. When the "
                        "thing has actually reached the person it was promised to, mark it "
                        "discharged and NAME YOURSELF:\n"
                        f"  UPDATE held_commitments SET status='discharged', discharged_at=now(), "
                        f"discharged_by='<your agent id>', discharge_note='<how>' WHERE id={c['id']};\n"
                        "Do not mark it discharged because you have read this message."
                    )
                    if not owned:
                        body += (
                            f"\n\n⚠⚠ ROUTED TO THE CONSOLE, NOT TO '{owner}': that id has no "
                            "agent_status row, so nothing drains its inbox. A message to an address "
                            "nothing owns cannot be late, only misaddressed. Re-own this commitment "
                            "or re-assign it."
                        )
                    if fire:
                        mid = _notify(cur, target, subject, body)
                        cur.execute(
                            "UPDATE held_commitments SET status='fired', fired_at=now(), "
                            "updated_at=now() WHERE id=%s AND status='pending'", (c["id"],))
                        print(f"FIRED #{c['id']} -> {target} (msg {mid}) — {c['title']}")
                    else:
                        print(f"[dry-run] would FIRE #{c['id']} -> {target} — {c['title']}")
                    fired += 1

                elif c["status"] == "fired" and c["fired_at"] is not None:
                    since = _dt.datetime.now(_dt.timezone.utc) - c["fired_at"]
                    if since < ESCALATE_AFTER:
                        continue
                    subject = (f"COMMITMENT #{c['id']} FIRED BUT NEVER DISCHARGED "
                               f"({int(since.total_seconds()//60)}m): {c['title']}")
                    body = (
                        f"Commitment #{c['id']} fired at {c['fired_at']:%H:%M:%S}Z and is still "
                        f"not discharged {since} later.\n\n"
                        f"  title:  {c['title']}\n  owner:  {owner}"
                        f"{'' if owned else '  ⚠ OWNS NO INBOX'}\n"
                        f"  due:    {c['due_at']:%Y-%m-%d %H:%M:%S}Z (late by {late})\n"
                        f"  source: {c['source_ref'] or '(none)'}\n\n"
                        "Either it was kept and nobody stamped it, or it was dropped. Both are "
                        "worth a minute. Assess and act, then record — and if it was kept, stamp "
                        "discharged_by so the next sweep stops asking."
                    )
                    if fire:
                        mid = _notify(cur, CONSOLE, subject, body, priority="P1")
                        cur.execute(
                            "UPDATE held_commitments SET fired_at=now(), updated_at=now() "
                            "WHERE id=%s", (c["id"],))
                        print(f"ESCALATED #{c['id']} -> {CONSOLE} (msg {mid})")
                    else:
                        print(f"[dry-run] would ESCALATE #{c['id']} -> {CONSOLE}")
                    escalated += 1

            if fire:
                conn.commit()
            else:
                conn.rollback()

    print(f"\ncommitment_sweeper: due={len(due)} fired={fired} escalated={escalated} "
          f"unowned_rerouted={skipped_unowned} mode={'FIRE' if fire else 'dry-run'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fire", action="store_true",
                    help="actually fire + notify (default is a dry-run that changes nothing)")
    args = ap.parse_args()
    return sweep(fire=args.fire)


if __name__ == "__main__":
    raise SystemExit(main())
