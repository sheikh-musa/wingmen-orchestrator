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
import os
import pathlib
import sys

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

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
    return live


def _notify(cur, to_agent: str, subject: str, body: str, priority: str = "P1") -> int:
    cur.execute(
        """INSERT INTO agent_messages
             (from_agent, to_agent, message_type, subject, body, requires_response, priority)
           VALUES ('orch-console', %s, 'update', %s, %s, false, %s)
           RETURNING id""",
        (to_agent, subject[:300], body, priority),
    )
    return cur.fetchone()["id"]


def sweep(fire: bool) -> int:
    dsn = _dsn()
    if not dsn:
        # Fail LOUD, never no-op silently — D6, and the behaviour A3 got right on its first run.
        print("commitment_sweeper: MISSING DSN — cannot run; refusing to no-op silently",
              file=sys.stderr)
        return 2

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
