#!/usr/bin/env bash
# send_control_buttons.sh — put the fleet's reset controls in the operator's Telegram thread
# as inline buttons, so clearing a body does not require remoting into a machine (op#7326).
#
# THE BUTTONS DO NOT RUN ANYTHING THEMSELVES. Pressing one sends a callback carrying an
# action KEY; nervous_system/ingest.py matches that key against its BUTTON_ACTIONS allowlist
# and runs the mapped script. Callback data never becomes a command, a path, or an argument,
# and only MUSA_TELEGRAM_ID is honoured — anyone else pressing a forwarded button is refused
# and audited.
#
# WHY THE INGEST EXECUTES AND NOT THE CONSOLE: Nazim cannot reset itself (CAI-500 cond 4 —
# a body that can clear its own context can clear itself out of an instruction). The ingest
# is a separate always-on process, so it can act on a body that is mid-turn or wedged.
#
# Usage: scripts/send_control_buttons.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
TOK=$(grep -m1 '^NAZIM_BOT_TOKEN=' .env | cut -d= -f2-)
CHAT=$(grep -m1 '^MUSA_TELEGRAM_ID=' .env | cut -d= -f2-)
[ -n "${TOK:-}" ] && [ -n "${CHAT:-}" ] || { echo "NAZIM_BOT_TOKEN / MUSA_TELEGRAM_ID missing" >&2; exit 1; }

TEXT="🎛 Fleet controls — press to clear a body. Each reboots from that body's newest handoff; the script refuses if it is mid-task or has no restore point."

# One row per body. callback_data must match a BUTTON_ACTIONS key in ingest.py exactly.
KEYBOARD='{"inline_keyboard":[
  [{"text":"🧹 Clear Nazim (console)","callback_data":"reset_nazim"}],
  [{"text":"🧹 Clear cai (governance)","callback_data":"reset_cai"}],
  [{"text":"🧹 Clear the hub","callback_data":"reset_orch"}]
]}'

curl -s --ipv4 "https://api.telegram.org/bot${TOK}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" \
  --data-urlencode "text=${TEXT}" \
  --data-urlencode "reply_markup=${KEYBOARD}" \
  | "$ORCH_DIR/.venv/bin/python3" -c 'import sys,json;d=json.load(sys.stdin);print("sent" if d.get("ok") else "FAILED: "+str(d.get("description")))'
