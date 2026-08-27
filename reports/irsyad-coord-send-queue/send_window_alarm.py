#!/usr/bin/env python3
# Shuk-schedule send-window alarm. Approved to send (orch-console #22690, op#13445 delegated
# wording to console; corrections applied). MUST send in SG business hours, not at ~6am Sunday.
# This sleeps until 01:30 UTC (= 09:30 SGT) then posts a P1 requires_response row to wake coord,
# who re-checks inbox (riba / Shuk-messaged-first / lane change) THEN sends verbatim from the file.
# It does NOT auto-send — judgment (Nazim's caveats) stays with coord.
import os, sys, time, datetime
sys.path.insert(0, '/Users/sheikhmusa/wingmen/orchestrator/scripts')
from agent_boot import _client

TARGET_UTC = datetime.datetime(2026, 8, 16, 1, 30, 0, tzinfo=datetime.timezone.utc)  # 09:30 SGT Sun
FILE = '/Users/sheikhmusa/wingmen/orchestrator/reports/irsyad-coord-send-queue/shuk_schedule_FINAL.txt'
c = _client()

now = datetime.datetime.now(datetime.timezone.utc)
wait = (TARGET_UTC - now).total_seconds()
print(f"send-window alarm armed: now={now.isoformat()} target={TARGET_UTC.isoformat()} wait={int(wait)}s", flush=True)
if wait > 0:
    time.sleep(wait)

body = (
    "SEND WINDOW OPEN (09:30 SGT Sun). Approved: orch-console #22690 (op#13445 delegated wording to console; "
    "both #22682 corrections already applied in the file). ACTION:\n"
    f"1. Re-check inbox first — if Shuk messaged, if any lane sent a correction, or anything changed, reconcile before sending.\n"
    f"2. Send VERBATIM from {FILE} via scripts/_tg_chunked_send.py (TG_TOK=IRSYAD_SUPPORT_BOT_TOKEN, TG_CHAT=-5330147776).\n"
    "3. Log operator_messages (direction=outbound, tag=gazzabyte-irsyad, from_name=cc-irsyad-coord).\n"
    "4. Post verbatim-sent confirmation to orch-console and mark #22690 responded.\n"
    "GATES STILL LIVE: %-of-balance fee answer => STOP, route cai (riba), do not soften. Only report lane dates you've CONFIRMED."
)
c.table('agent_messages').insert({
    'from_agent': 'cc-irsyad-coord', 'to_agent': 'cc-irsyad-coord',
    'message_type': 'blocker', 'priority': 'P1', 'requires_response': True,
    'subject': 'SEND WINDOW: Shuk schedule ready to send (09:30 SGT) — re-check inbox then send verbatim',
    'body': body, 'posted_by_identity': 'cc-irsyad-coord',
}).execute()
print("send-window alarm FIRED: woke coord to send.", flush=True)
