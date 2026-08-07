#!/usr/bin/env bash
# Send a Telegram message to Zahidah via @mamadahbot.
# Usage: scripts/tg_send_mamadah.sh <chat_id> "<text>"
set -euo pipefail

CHAT_ID="${1:-}"
TEXT="${2:-}"

if [[ -z "$CHAT_ID" || -z "$TEXT" ]]; then
  echo "Usage: tg_send_mamadah.sh <chat_id> <text>" >&2
  exit 1
fi

# Load bot token
if [[ -f "$HOME/.wingmen/keys/nutri-science-study-bot.env" ]]; then
  source "$HOME/.wingmen/keys/nutri-science-study-bot.env"
fi
if [[ -z "${NUTRI_STUDY_BOT_TOKEN:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [[ -f "$SCRIPT_DIR/../.env" ]] && source "$SCRIPT_DIR/../.env"
fi

if [[ -z "${NUTRI_STUDY_BOT_TOKEN:-}" ]]; then
  echo "NUTRI_STUDY_BOT_TOKEN not set" >&2
  exit 1
fi

# Pass text via env var to avoid shell injection
MAMADAH_TOKEN="$NUTRI_STUDY_BOT_TOKEN" MAMADAH_CHAT="$CHAT_ID" MAMADAH_TEXT="$TEXT" \
python3 - << 'PYEOF'
import os, urllib.request, json, sys
token = os.environ["MAMADAH_TOKEN"]
chat_id = os.environ["MAMADAH_CHAT"]
text = os.environ["MAMADAH_TEXT"][:4000]
req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage",
    data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print("sent", r.status)
except Exception as e:
    print("error", e, file=sys.stderr)
    sys.exit(1)
PYEOF
