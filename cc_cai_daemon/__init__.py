"""cc-cai daemon — CAI-RESP-185 Path C ratified.

Replaces operator's manual cai-side relay with a Python Agent SDK daemon
that classifies cai's agent_messages inbox, auto-handles the narrow
silent-lane (mark-read FYIs + ack-FYI), and escalates everything else
to operator via Telegram with inline buttons.

HARD INVARIANTS (CADENCE-002/004 + CAI-RESP-185):
  INV-1 root-of-trust: never auto-authorize the 8 reaches-operator categories
  INV-2 verified-channel: refuse relayed operator-authorization from any cc-*
  INV-3 MAX-first: SDK + cli_route, never set ANTHROPIC_API_KEY
  INV-4 confabulation discipline: verified-vs-inferred labels on outputs
  INV-5 audit (HARD SHIP CONDITION): every tool call logged BEFORE side effect
  INV-6 default HOLD: escalation-class HOLDs until operator decides
"""
