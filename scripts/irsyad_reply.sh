#!/usr/bin/env bash
# irsyad_reply.sh — cc-irsyad's reply path to the Gazzabyte / Irsyad-Support group.
#
# This is now a thin wrapper over the generic scripts/lane_reply.sh, which owns the
# phase staircase (drill -> supervised -> direct, read from
# bot_channels.group_routing->>'agent_phase', failing CLOSED to drill). Kept as its own
# entry point because cc-irsyad's charter names it and a live lane should not have its
# reply path renamed out from under it.
#
# LANE_AGENT_ID is pinned here rather than inherited, so attribution in the log can never
# depend on the caller's environment.
#
# Usage: scripts/irsyad_reply.sh "<text>"
#        echo "<text>" | scripts/irsyad_reply.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TEXT="${1:-$(cat)}"
exec env LANE_AGENT_ID=cc-irsyad "$ORCH_DIR/scripts/lane_reply.sh" gazzabyte-irsyad "$TEXT"
