#!/usr/bin/env python3
"""PreToolUse guard: the console body (Nazim) stays OFF irsyad work — enforced, not remembered.

Musa op#21944 (2026-09-22) + op#22229/22233 (2026-09-24, second relapse): irsyad investigation,
design, code review and data access belong to cc-irsyad-coord + its musa2 lanes. The console's
only irsyad role is a yes/no floor ruling on coord's EVIDENCE PACK, which arrives over the bus
(agent_messages) — so the console never needs the irsyad silos or the ihsanos code.

Scope: blocks ONLY when the claude process itself is the console — ORCH_BODY_ROLE=console AND no
CC_BASE_AGENT_ID. Lanes (coord included) carry CC_BASE_AGENT_ID and are exempt. The hook reads the
claude process env it inherits, so a command that re-sources .env cannot change the verdict
(see reference_lane_vs_console_discriminator_is_cc_base_agent_id).

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
"""
import json
import os
import re
import sys

BLOCKED = [
    (r"goumlynecruxrlmzlntp|GOUMLYNE_", "irsyad silo (goumlyne)"),
    (r"ceayjeamtmcyzzvqflus|IHSANOS_PROD_DATABASE_URL", "ihsanos multi-tenant DB (ceayj)"),
    (r"write_dsn\.env|WRITE_DSN_ALLOWED", "client-silo write DSN"),
    (r"sheikh-musa/ihsanos\b", "ihsanos repo (irsyad code)"),
    (r"wingmen/projects/ihsanos", "ihsanos checkout (irsyad code)"),
]


def is_console() -> bool:
    return os.environ.get("ORCH_BODY_ROLE") == "console" and not os.environ.get("CC_BASE_AGENT_ID")


def main() -> int:
    if not is_console():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = payload.get("tool_input") or {}
    haystack = json.dumps(tool_input)
    for pattern, what in BLOCKED:
        if re.search(pattern, haystack):
            sys.stderr.write(
                f"BLOCKED by console_irsyad_guard: this touches the {what}. The console stays off irsyad "
                "(Musa op#21944/#22229). Route it to cc-irsyad-coord via _bus_tmp.post(); if a gate needs "
                "evidence, ask coord for it, never fetch it yourself.\n"
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
