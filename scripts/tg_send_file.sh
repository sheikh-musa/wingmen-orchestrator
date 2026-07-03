#!/usr/bin/env bash
# tg_send_file.sh — send a FILE (PDF, image, report, zip…) to the operator via
# the Wingmen Orchestrator bot (@wingmennorchbot). The outbound-file half of the
# bridge: when a lane produces a deliverable (e.g. a sample namelist PDF),
# cc-orchestrator sends it straight to the operator's phone.
#
# Usage:
#   scripts/tg_send_file.sh <filepath> [caption]
#
# Reads WINGMEN_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. Logs to operator_messages.
# Telegram bot sendDocument limit is 50 MB.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^WINGMEN_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# Default target = the operator (Musa). TG_CHAT_OVERRIDE routes a file to another
# authorized chat (e.g. the shipforge/storefront group).
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "WINGMEN_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

FILE="${1:?usage: tg_send_file.sh <filepath> [caption]}"
CAPTION="${2:-}"
[ -f "$FILE" ] || { echo "file not found: $FILE" >&2; exit 1; }

# Size guard (Telegram bot cap is 50MB)
bytes=$(stat -f%z "$FILE" 2>/dev/null || stat -c%s "$FILE" 2>/dev/null || echo 0)
if [ "$bytes" -gt 52428800 ]; then
  echo "file too large for Telegram (${bytes} bytes > 50MB)" >&2; exit 1
fi

resp=$(curl -s "https://api.telegram.org/bot${TOK}/sendDocument" \
  -F "chat_id=${CHAT}" \
  -F "document=@${FILE}" \
  ${CAPTION:+-F "caption=${CAPTION}"})
ok=$(printf '%s' "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('ok') else '0:'+str(d.get('description')))")

# durable log (best-effort)
"$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound \
  "[file] $(basename "$FILE")${CAPTION:+ — $CAPTION}" --chat "$CHAT" >/dev/null 2>&1 || true

case "$ok" in
  1) exit 0 ;;
  *) echo "tg_send_file error: ${ok#0:}" >&2; exit 1 ;;
esac
