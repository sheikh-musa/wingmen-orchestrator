#!/usr/bin/env python3
# Manual-key-in "yes" unbundle-check alarm (console #22731 time-bound).
# Default = bundle the manual-key-in confirmation WITH the template send. But do NOT let a
# blocked template hold a plain yes to a direct question all day. This wakes coord at ~09:30 SGT
# (Shuk's working day) to decide: if the template has cleared -> bundle; if still blocked ->
# send the manual-key-in confirmation STANDALONE. Does NOT auto-send; judgment stays with coord.
import os, sys, time, datetime
sys.path.insert(0, '/Users/sheikhmusa/wingmen/orchestrator/scripts')
from agent_boot import _client

TARGET_UTC = datetime.datetime(2026, 8, 16, 1, 30, 0, tzinfo=datetime.timezone.utc)  # 09:30 SGT Sun
c = _client()
now = datetime.datetime.now(datetime.timezone.utc)
wait = (TARGET_UTC - now).total_seconds()
print(f"unbundle-check armed: target={TARGET_UTC.isoformat()} wait={int(wait)}s", flush=True)
if wait > 0:
    time.sleep(wait)

body = (
    "UNBUNDLE CHECK (09:30 SGT, console #22731 time-bound). Shuk is into his working day. Decide the "
    "manual-key-in 'yes':\n"
    "1. Has the PII template cleared (cai re-review #22748 done AND operator gave registered entity names)? "
    "If YES -> bundle the manual-key-in confirmation WITH the template send.\n"
    "2. If template STILL blocked -> send the manual-key-in confirmation STANDALONE now (I already told him "
    "'I'll confirm once pinned down'; cc-irsyad-5 #22727 confirmed it's the core of Phase-2 by design). "
    "Frame: 'the counting screen is manual key-in by design, so you're covered' — do NOT imply Cisco go-live "
    "(A4 fee gate still open). Log operator_messages, no double-send (read sender's own exit, no grep pipe).\n"
    "Console pre-blessed the content + left the exact moment to my judgment."
)
c.table('agent_messages').insert({
    'from_agent': 'cc-irsyad-coord', 'to_agent': 'cc-irsyad-coord',
    'message_type': 'update', 'priority': 'P2', 'requires_response': True,
    'subject': 'UNBUNDLE CHECK 09:30 SGT — send manual-key-in "yes" standalone if template still blocked',
    'body': body, 'posted_by_identity': 'cc-irsyad-coord',
}).execute()
print("unbundle-check FIRED: woke coord.", flush=True)
