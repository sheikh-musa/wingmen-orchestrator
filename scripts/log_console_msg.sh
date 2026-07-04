#!/usr/bin/env bash
# log_console_msg.sh — R2 (CAI-RESP-377): EVERY operator surface logs BEFORE relay.
# When the operator types into the orch tmux console (or any non-bridged surface),
# the receiving agent durably logs his words to operator_messages FIRST — an
# unlogged operator surface is a forbidden surface (Option B applies everywhere).
#
# Usage: scripts/log_console_msg.sh "<operator's words verbatim>" [tag]
# Prints the operator_messages row id.
set -euo pipefail
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEXT="${1:?usage: log_console_msg.sh \"<text>\" [tag]}"
TAG="${2:-}"
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  inbound "$TEXT" --channel tmux-console ${TAG:+--tag "$TAG"}
