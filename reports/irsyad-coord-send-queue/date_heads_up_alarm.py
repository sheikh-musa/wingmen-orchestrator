#!/usr/bin/env python3
# Date heads-up fallback alarm (console #23005). We promised Shuk "I'll tell you the moment I THINK
# it's moving." The operator-approved same-signer waiver UNSETTLED the approval-flow date. Rule:
# the moment cc-irsyad-5 answers → tell Shuk either way; if -5 is silent by ~30 min from the ask,
# tell him anyway ("the change you asked for may affect the approval part; we're confirming the date;
# the screen you key into is still Monday 18"). This wakes coord at 03:55 UTC to send that fallback
# if -5 still hasn't answered. Does NOT auto-send — coord drafts + clears wording with console.
import os, sys, time, datetime
sys.path.insert(0, '/Users/sheikhmusa/wingmen/orchestrator/scripts')
from agent_boot import _client
TARGET = datetime.datetime(2026, 8, 16, 3, 55, 0, tzinfo=datetime.timezone.utc)
c = _client()
now = datetime.datetime.now(datetime.timezone.utc)
wait = (TARGET - now).total_seconds()
print(f"date heads-up alarm armed: target={TARGET.isoformat()} wait={int(wait)}s", flush=True)
if wait > 0:
    time.sleep(wait)
body = (
    "DATE HEADS-UP DEADLINE (~30min, console #23005). If cc-irsyad-5 has ANSWERED the date-impact "
    "question by now → relay to Shuk EITHER WAY (no-move='still Monday' pre-cleared; move → bring wording "
    "to console, fact-and-trade framing, lead with 'counting/charge/net screen still Mon 18 flat'). "
    "If -5 STILL silent → send Shuk the confirming-the-date heads-up (draft + clear wording with console "
    "first): 'the change you asked for may affect the approval part specifically; the screen you key into "
    "is still Monday the 18th; I'm confirming the exact date and will tell you.' Honour the 'moment I think "
    "it's moving' promise without inventing a slip."
)
c.table('agent_messages').insert({
    'from_agent': 'cc-irsyad-coord', 'to_agent': 'cc-irsyad-coord',
    'message_type': 'blocker', 'priority': 'P1', 'requires_response': True,
    'subject': 'DATE HEADS-UP deadline (~03:55) — relay -5 answer either way, or send confirming-date fallback (via console)',
    'body': body, 'posted_by_identity': 'cc-irsyad-coord',
}).execute()
print("date heads-up alarm FIRED: woke coord.", flush=True)
