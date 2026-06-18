#!/usr/bin/env bash
# tg_send.sh — send a message to the operator via the Wingmen Orchestrator bot
# (@wingmennorchbot). The outbound half of the 2-way bridge: cc-orchestrator
# (and the daily brief) call this to reach the operator's phone.
#
# Reads WINGMEN_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:
#   scripts/tg_send.sh "message text"
#   echo "message" | scripts/tg_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^WINGMEN_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT=$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)
[ -n "${TOK:-}" ] || { echo "WINGMEN_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

TEXT="${1:-$(cat)}"
TAG="${2:-}"   # optional @alias context this reply pertains to
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

# Send (chunked at Telegram's 4096-char limit so long replies aren't truncated).
# token/chat/text passed via env, never argv — keeps the token out of `ps`.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# durable log every reply (full text, once; best-effort — never fail on a log hiccup)
"$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" ${TAG:+--tag "$TAG"} >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "tg_send failed" >&2; exit 1; }
