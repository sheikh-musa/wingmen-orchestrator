#!/usr/bin/env python3
# CAI-921 C3 first-REAL-donor-send verify monitor (v2, post-backfill).
# The 990 backfill (issued_by=ca2a4a9c, emailed_at NULL) is DONE + safe.
# The real C3 gate = the FIRST staff/donor-issued receipt (issued_by != ca2a4a9c)
# that should auto-send. This watches for it and wakes coord to verify at source.
import os, sys, time
sys.path.insert(0, '/Users/sheikhmusa/wingmen/orchestrator/scripts')
from agent_boot import _client
import psycopg

DB  = os.environ['GOUMLYNE_DATABASE_URL']
ORG = '73339164-7c1f-40ba-a093-33f1f292dd4c'
BACKFILL_AGENT = 'ca2a4a9c-f745-40c3-9536-7a021fd42bb9'
POLL = 20
WINDOW = 86400   # 24h; re-arm on elapse until the first real send is verified
c = _client()

def snap(cur):
    cur.execute("SELECT count(*) FROM receipts WHERE org_id=%s AND issued_by IS DISTINCT FROM %s", (ORG, BACKFILL_AGENT))
    return cur.fetchone()[0]

with psycopg.connect(DB, connect_timeout=15, autocommit=True) as conn:
    with conn.cursor() as cur:
        base = snap(cur)
        print(f"C3 v2 armed: org 73339164 non-backfill (real) receipts baseline={base} (990 backfill excluded)", flush=True)
        end = time.time() + WINDOW
        fired = False
        while time.time() < end:
            time.sleep(POLL)
            if snap(cur) > base:
                cur.execute(
                    "SELECT id, receipt_number, emailed_at, status, issued_by, donation_id, created_at "
                    "FROM receipts WHERE org_id=%s AND issued_by IS DISTINCT FROM %s "
                    "ORDER BY created_at DESC LIMIT 1", (ORG, BACKFILL_AGENT))
                r = cur.fetchone()
                body = (
                    f"C3 FIRST REAL DONOR RECEIPT on 73339164 (CAI-921 final live gate) — coord VERIFY.\n"
                    f"  id={r[0]} #{r[1]} emailed_at={r[2]} status={r[3]} issued_by={r[4]} donation={r[5]} created={r[6]}\n\n"
                    f"C3 CHECK: emailed_at SET => send accepted, NOT silent-caught => GOOD (confirm Resend delivered via Shuk/operator dashboard). "
                    f"emailed_at NULL past 15min + donor-has-email => silent-catch (CAI-908/909) => HALT + re-freeze receipt:send=none + report cai. Report either way."
                )
                c.table('agent_messages').insert({
                    'from_agent': 'cc-irsyad-coord', 'to_agent': 'cc-irsyad-coord',
                    'message_type': 'blocker', 'priority': 'P1', 'requires_response': True,
                    'subject': f'C3 v2: first REAL donor receipt on 73339164 (#{r[1]}, emailed_at={"SET" if r[2] else "NULL"}) — verify or HALT',
                    'body': body, 'posted_by_identity': 'cc-irsyad-coord',
                }).execute()
                print(f"C3 v2 FIRED: real receipt {r[0]} #{r[1]} emailed_at={r[2]}; woke coord.", flush=True)
                fired = True
                break
        if not fired:
            print("C3 v2 window elapsed; no real donor send yet; re-arm if go-live still open.", flush=True)
