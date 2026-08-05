#!/usr/bin/env bash
# reviewer_send.sh — generic reviewer send into ANY bot_channels group, as orch-console
# (Nazim, the human-owned reviewer). Generalises caai_send.sh: instead of one script per
# channel, this looks up token_env_key + allowed_chat_ids from bot_channels for the given
# channel_key, so it serves cosem-exams (Hariz), alderei (Nahar), cosem-caai (Ray), etc.
#
# The supervised phase gates a LANE's replies (lane_reply.sh); this is the reviewer's own
# voice and does NOT go through the lane gate. Mirrors irsyad_support_send.sh incl. CAI-598
# delivery-logging (log DELIVERY, not intent). NEVER echoes the token.
#
# Usage:  scripts/reviewer_send.sh <channel_key> "message text"
#         echo "message" | scripts/reviewer_send.sh <channel_key>
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
CHANNEL="${1:?usage: reviewer_send.sh <channel_key> \"message\"}"
TEXT="${2:-$(cat)}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

# Resolve token_env_key + chat_id from bot_channels (single source of truth).
read -r TOKEN_KEY CHAT < <(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$CHANNEL" <<'PY'
import sys, psycopg
from nervous_system.nazim_bus_notify import _dsn
ch = sys.argv[1]
with psycopg.connect(_dsn()) as c:
    r = c.execute("SELECT token_env_key, allowed_chat_ids FROM bot_channels WHERE channel_key=%s", (ch,)).fetchone()
if not r or not r[0] or not r[1]:
    sys.exit(2)
print(r[0], r[1][0])
PY
) || { echo "reviewer_send: channel '$CHANNEL' has no token_env_key/allowed_chat_ids in bot_channels" >&2; exit 2; }

TOK=$(grep -m1 "^${TOKEN_KEY}=" "$ORCH_DIR/.env" | cut -d= -f2-)
[ -n "${TOK:-}" ] || { echo "reviewer_send: ${TOKEN_KEY} missing from .env" >&2; exit 1; }

# Send (chunked at Telegram's 4096-char limit). token/chat/text via env, never argv.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# Durable log — CAI-598: record delivery, not intent.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag "$CHANNEL" $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "reviewer_send to '$CHANNEL' failed" >&2; exit 1; }
