#!/usr/bin/env python3
"""fleet_stall_watch.py — catch ZOMBIE lanes: alive + heartbeating but producing nothing.

The heartbeat watchdog + reaper only distinguish alive vs dead. A lane can heartbeat
'working' every 3 min yet ingest/produce nothing for days (e.g. cc-cosem-adcda blocked
on a decision on 2026-07-04, never re-tasked — invisible to every existing watcher).
This is the missing third signal beside ALIVE (heartbeat) and INGESTING (inbox read):
PRODUCING (last output).

Runs on a launchd timer (Mac Mini — always-on, hub-independent). Pure substrate, no tmux.
On a NEW *sustained* stall of a desired-up lane it posts ONE P1 digest
(orch-console -> cc-orchestrator) which the notifier forwards to the operator's Telegram
(mechanical page, not the hub's reasoning) AND the hub can action (re-nudge the lane).

Guards against false pages (the ihsanos.com HTTP-000 lesson):
  - HYSTERESIS: a lane must be stalled across >=2 consecutive scans before it alerts.
  - DE-DUPE: alert once per stall episode; the row clears when the lane produces again.

  python3 scripts/fleet_stall_watch.py [--dry-run]
Env: FLEET_STALL_HOURS (default 8) · FLEET_STALL_HB_MIN (default 20)
"""
import os
import sys
import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
STALL_HOURS = int(os.environ.get("FLEET_STALL_HOURS", "8"))
HB_MIN = int(os.environ.get("FLEET_STALL_HB_MIN", "20"))
DRY = "--dry-run" in sys.argv

DDL = """
CREATE TABLE IF NOT EXISTS fleet_stall_state (
  lane              text PRIMARY KEY,
  stalled_since     timestamptz NOT NULL DEFAULT now(),
  last_seen_stalled timestamptz NOT NULL DEFAULT now(),
  alerted_at        timestamptz,
  last_produce      timestamptz
);
"""

# Alive + desired-up lanes that have produced nothing for STALL_HOURS.
# Desired-up excludes down / on-demand / retired identities; the heartbeat JOIN
# excludes dead identities (those are the reaper's job, not zombies).
CURRENT_STALL_SQL = f"""
WITH pri AS (
    SELECT DISTINCT base_agent_id AS lane FROM fleet_lanes
    WHERE desired_state = 'up' AND base_agent_id IS NOT NULL
),
hb AS (
    SELECT regexp_replace(agent_id, '-[0-9]+$', '') AS lane, max(last_heartbeat) AS h
    FROM agent_status GROUP BY 1
),
prod AS (
    SELECT from_agent AS lane, max(created_at) AS p FROM agent_messages GROUP BY from_agent
)
SELECT pri.lane, hb.h AS last_hb, prod.p AS last_produce,
       round(extract(epoch FROM (now() - prod.p)) / 3600, 1) AS produce_hrs
FROM pri
JOIN hb ON hb.lane = pri.lane
LEFT JOIN prod ON prod.lane = pri.lane
WHERE hb.h > now() - interval '{HB_MIN} minutes'
  AND (prod.p IS NULL OR prod.p < now() - interval '{STALL_HOURS} hours')
ORDER BY prod.p ASC NULLS FIRST
"""


def fmt(ts):
    return ts.strftime("%m-%d %H:%M") if ts else "never"


def main():
    if not DSN:
        print("no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
        cur.execute(DDL)
        cur.execute(CURRENT_STALL_SQL)
        stalled = {r[0]: {"last_produce": r[2], "hrs": r[3]} for r in cur.fetchall()}

        if DRY:
            print(f"[dry-run] STALL_HOURS={STALL_HOURS} HB_MIN={HB_MIN}")
            if not stalled:
                print("  no desired-up lane is currently stalled — fleet producing.")
            for lane, d in stalled.items():
                print(f"  STALLED {lane:20s} no output for {d['hrs']}h (last {fmt(d['last_produce'])})")
            print(f"  → would track {len(stalled)}; each alerts on its 2nd consecutive stalled scan (hysteresis).")
            return 0

        cur.execute("SELECT lane FROM fleet_stall_state")
        prior = {r[0] for r in cur.fetchall()}

        # upsert current stalls (idempotent; stalled_since preserved for hysteresis)
        for lane, d in stalled.items():
            cur.execute("""INSERT INTO fleet_stall_state (lane, last_produce) VALUES (%s, %s)
                           ON CONFLICT (lane) DO UPDATE SET last_seen_stalled=now(),
                             last_produce=EXCLUDED.last_produce""",
                        (lane, d["last_produce"]))

        # recoveries: previously tracked, now producing again -> clear
        recovered = sorted(prior - set(stalled))
        for lane in recovered:
            cur.execute("DELETE FROM fleet_stall_state WHERE lane=%s", (lane,))

        # confirmed (>=2 scans) + not-yet-alerted stalls
        cur.execute("""SELECT lane FROM fleet_stall_state
                       WHERE alerted_at IS NULL AND stalled_since < now() - interval '5 minutes'""")
        to_alert = [r[0] for r in cur.fetchall() if r[0] in stalled]

        alerted_id = None
        if to_alert:
            lines = "\n".join(f"- {l} : no output for {stalled[l]['hrs']}h (last {fmt(stalled[l]['last_produce'])})"
                              for l in to_alert)
            subject = f"FLEET STALL - {len(to_alert)} priority lane(s) alive but producing nothing: {', '.join(to_alert)}"
            body = ("Productivity-watcher (fleet_stall_watch) caught zombie lanes the heartbeat-watchdog is blind to. "
                    f"These desired-up lanes heartbeat 'working' but have produced NOTHING for >{STALL_HOURS}h:\n\n"
                    f"{lines}\n\nACTION (hub, pen ii): re-nudge each so it drains its bus + resumes. "
                    "Cross-check: SELECT * FROM fleet_drain_freshness;")
            cur.execute("""INSERT INTO agent_messages
                             (from_agent,to_agent,thread_id,message_type,subject,body,requires_response,priority,is_test)
                           VALUES ('orch-console','cc-orchestrator',gen_random_uuid(),'update',%s,%s,true,'P1',false)
                           RETURNING id""", (subject, body))
            alerted_id = cur.fetchone()[0]
            cur.execute("UPDATE fleet_stall_state SET alerted_at=now() WHERE lane = ANY(%s)", (to_alert,))

        # recovery note (only for lanes we'd actually alerted before is unknown here; keep it simple: note recoveries)
        recovery_id = None
        if recovered:
            subject = f"Fleet recovery - {len(recovered)} lane(s) producing again: {', '.join(recovered)}"
            body = "fleet_stall_watch: these lanes are producing again and have cleared the stall list:\n\n" + \
                   "\n".join(f"- {l}" for l in recovered)
            cur.execute("""INSERT INTO agent_messages
                             (from_agent,to_agent,thread_id,message_type,subject,body,requires_response,priority,is_test)
                           VALUES ('orch-console','cc-orchestrator',gen_random_uuid(),'update',%s,%s,false,'P2',false)
                           RETURNING id""", (subject, body))
            recovery_id = cur.fetchone()[0]

        conn.commit()
        print(f"stalled now ({len(stalled)}): {sorted(stalled)}")
        print(f"alerted this run: {to_alert or '(none — hysteresis or already alerted)'}"
              + (f" -> #{alerted_id}" if alerted_id else ""))
        print(f"recovered this run: {recovered or '(none)'}" + (f" -> #{recovery_id}" if recovery_id else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
