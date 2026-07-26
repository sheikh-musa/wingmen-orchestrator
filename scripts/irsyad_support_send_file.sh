#!/usr/bin/env bash
# irsyad_support_send_file.sh — send a FILE (PDF, image, report, count-sheet…)
# to the Gazzabyte / Irsyad-Support group via the Irsyad-Support bot. The file
# counterpart to irsyad_support_send.sh (text). This is how cc-orchestrator (the
# human-owned hub) delivers documents INTO the external vendor (Gazzabyte) group.
#
# SECURITY (perimeter): default target is the Gazzabyte group ONLY
# (IRSYAD_SUPPORT_GROUP_CHAT_ID). Reads IRSYAD_SUPPORT_BOT_TOKEN from .env.
# NEVER echoes the token.
# Usage:
#   scripts/irsyad_support_send_file.sh <filepath> [caption]
#   TG_CHAT_OVERRIDE=<chat_id> scripts/irsyad_support_send_file.sh <filepath> [caption]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^IRSYAD_SUPPORT_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^IRSYAD_SUPPORT_GROUP_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ]  || { echo "IRSYAD_SUPPORT_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "IRSYAD_SUPPORT_GROUP_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }

FILE="${1:?usage: irsyad_support_send_file.sh <filepath> [caption]}"
CAPTION="${2:-}"
[ -f "$FILE" ] || { echo "File not found: $FILE" >&2; exit 1; }
# Telegram sendDocument hard limit is 50 MB.
SZ=$(wc -c < "$FILE" | tr -d ' ')
[ "$SZ" -le 52428800 ] || { echo "File too large ($SZ bytes > 50MB Telegram limit): $FILE" >&2; exit 1; }

ARGS=(-s --ipv4 -X POST "https://api.telegram.org/bot${TOK}/sendDocument"
  -F "chat_id=${CHAT}"
  -F "document=@${FILE}")
[ -n "$CAPTION" ] && ARGS+=(-F "caption=${CAPTION}")

resp=$(curl "${ARGS[@]}")
ok=$(printf '%s' "$resp" | "$ORCH_DIR/.venv/bin/python3" -c 'import sys,json;print(json.load(sys.stdin).get("ok"))' 2>/dev/null || echo "False")

# durable log every outbound file (best-effort — never fail on a log hiccup).
NOTE="sent a FILE → $(basename "$FILE")$([ -n "$CAPTION" ] && echo "  | caption: $CAPTION")"
# CAI-598/600: log DELIVERY, not intent — operator_log defaults delivered=TRUE.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$NOTE" --chat "$CHAT" --tag "gazzabyte-irsyad" $([ "$ok" = "True" ] || echo --undelivered) >/dev/null 2>&1 || true

if [ "$ok" = "True" ]; then
  echo "sent: $(basename "$FILE") -> $CHAT"
  exit 0
else
  echo "irsyad_support_send_file failed: $resp" >&2
  exit 1
fi
