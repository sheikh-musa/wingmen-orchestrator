#!/usr/bin/env bash
# nazim_send_file.sh — Nazim (console/CTO body) → operator DOCUMENT via @nazim_cto_bot.
#
# Document counterpart to nazim_send_photo.sh: sends an arbitrary file (zip, svg,
# pdf, etc.) on Nazim's OWN Telegram channel (NAZIM_BOT_TOKEN / nazim-console),
# structurally outside the hub's pen-(iv) gate (CAI-RESP-426). Use to hand the
# operator actual deliverable files (he has Mini access, not Studio — Telegram
# delivery works regardless of filesystem/SSH).
#
# Reads NAZIM_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:  scripts/nazim_send_file.sh <file-path> ["caption"]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^NAZIM_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
DOC="${1:?usage: nazim_send_file.sh <file-path> [caption]}"
CAP="${2:-}"
[ -n "${TOK:-}" ]  || { echo "NAZIM_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }
[ -f "$DOC" ]      || { echo "file not found: $DOC" >&2; exit 1; }

code=$(curl -s -o /dev/null -w "%{http_code}" \
  -F "chat_id=${CHAT}" -F "document=@${DOC}" -F "caption=${CAP}" \
  "https://api.telegram.org/bot${TOK}/sendDocument" --max-time 120)

# Durable log (tag=nazim-console) so a rebooted Nazim sees the file went out.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[file] $(basename "$DOC")${CAP:+ — $CAP}" --chat "$CHAT" --tag nazim-console >/dev/null 2>&1 || true

[ "$code" = "200" ] && { echo "sent $(basename "$DOC")"; exit 0; } || { echo "nazim_send_file failed (HTTP $code)" >&2; exit 1; }
