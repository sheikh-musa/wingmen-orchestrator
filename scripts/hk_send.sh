#!/usr/bin/env bash
# hk_send.sh — post a text message to the Hadramawt Kitchen editor Telegram group
# as @hadramawtkitchenbot. OUTBOUND half of the HK conversational-editor channel
# (op#13082, agent-in-loop / irsyad mechanic). The acting agent (cc-ihsanos) calls
# this to confirm edits back to Afifah in-group. INBOUND is the nazim-ingest poll of
# bot_channels 'hk-editor' (enabled=false + pinned in INGEST_CHANNELS so only the Mini
# daemon polls it — no hub 409) which nudges the ihsanos-platform lane.
#
# Usage:  scripts/hk_send.sh "<message text>"
#   env:  HK_CHAT_ID overrides the target group (default = the HK group);
#         HK_EDIT_BOT_TOKEN (from .env) is the bot token.
set -euo pipefail
cd "$HOME/wingmen/orchestrator"
set -a; source .env; set +a

CHAT_ID="${HK_CHAT_ID:--5377033587}"                 # HK group (Musa + Afifah + bot)
TOK="${HK_EDIT_BOT_TOKEN:?HK_EDIT_BOT_TOKEN not set in .env}"
TEXT="${1:?usage: hk_send.sh \"<message>\"}"

resp="$(curl -sS -X POST "https://api.telegram.org/bot${TOK}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${TEXT}")"
if printf '%s' "$resp" | grep -q '"ok":true'; then
  echo "[hk_send] sent to ${CHAT_ID}"
else
  echo "[hk_send] FAILED: $resp" >&2
  exit 1
fi
