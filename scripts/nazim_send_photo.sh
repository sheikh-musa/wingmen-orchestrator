#!/usr/bin/env bash
# nazim_send_photo.sh — Nazim (console/CTO body) → operator PHOTO via @nazim_cto_bot.
#
# Photo counterpart to nazim_send.sh: sends an image on Nazim's OWN Telegram
# channel (NAZIM_BOT_TOKEN / nazim-console), structurally outside the hub's
# pen-(iv) gate (CAI-RESP-426). Use to relay screenshots/visual proof to the
# operator — always eyeball the image first (feedback_always_send_screenshots).
#
# Reads NAZIM_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:  scripts/nazim_send_photo.sh <image-path> ["caption"]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^NAZIM_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
IMG="${1:?usage: nazim_send_photo.sh <image-path> [caption]}"
CAP="${2:-}"
[ -n "${TOK:-}" ] || { echo "NAZIM_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }
[ -f "$IMG" ]      || { echo "image not found: $IMG" >&2; exit 1; }

# bus #44576: a real filename can contain ',' or ';', both special to curl's own
# -F parser — route through a syntax-safe staged path instead of @${IMG} directly.
source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"
tg_safe_upload_stage photo "$IMG" || { echo "nazim_send_photo: could not stage upload for $IMG" >&2; exit 1; }
trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT

code=$(curl -s -o /dev/null -w "%{http_code}" \
  -F "chat_id=${CHAT}" -F "$TG_SAFE_UPLOAD_FORM" -F "caption=${CAP}" \
  "https://api.telegram.org/bot${TOK}/sendPhoto" --max-time 30) || code="curl_exit_$?"

# Durable log (tag=nazim-console) so a rebooted Nazim sees the photo went out.
# CAI-598/600: log DELIVERY, not intent — operator_log defaults delivered=TRUE, so an
# unconditional call records a FAILED send as delivered.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[photo] $(basename "$IMG")${CAP:+ — $CAP}" --chat "$CHAT" --tag nazim-console \
  $([ "$code" = "200" ] || echo --undelivered) >/dev/null 2>&1 || true

[ "$code" = "200" ] && { echo "sent $(basename "$IMG")"; exit 0; } || { echo "nazim_send_photo failed (status: $code)" >&2; exit 1; }
