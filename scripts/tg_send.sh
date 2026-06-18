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

resp=$(curl -s "https://api.telegram.org/bot${TOK}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" \
  --data-urlencode "text=${TEXT}")
# report ok/error without ever surfacing the token
ok=$(printf '%s' "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('ok') else '0:'+str(d.get('description')))")
# durable log every reply (best-effort — never fail the send on a log hiccup)
"$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" ${TAG:+--tag "$TAG"} >/dev/null 2>&1 || true
case "$ok" in
  1) exit 0 ;;
  *) echo "tg_send error: ${ok#0:}" >&2; exit 1 ;;
esac
