#!/usr/bin/env bash
# No-echo, no-argv secret intake into the fleet vault (Musa op#35814, bus
# #82358): the sanctioned way to hand a brand-new secret to the fleet
# without ever pasting it into Telegram, a bus message, a CLI argument, or
# anything that lands in shell history or an agent transcript -- the exact
# gap that leak-flagged DEEPSEEK_CODING_KEY.
#
# Run this in a REAL terminal (your own shell on the Mini, over SSH or
# screen share) -- never inside an agent's tmux session, since that enters
# a transcript. The secret value is typed at an interactive, no-echo
# prompt; it is NEVER a command-line argument.
#
# Usage:
#   CC_BASE_AGENT_ID=musa scripts/vault_put_prompt.sh NAME "reason text"
#
# CC_BASE_AGENT_ID (or AGENT_ID) identifies who did the write in the vault's
# audit log -- required, fails loud if unset (vault.py's own identity gate).
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: CC_BASE_AGENT_ID=<you> $0 NAME \"reason text\"" >&2
    exit 1
fi

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/vault_put_prompt.py" "$1" "$2"
