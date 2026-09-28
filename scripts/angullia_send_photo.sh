#!/usr/bin/env bash
# angullia_send_photo.sh — send a PHOTO to the angullia channel, photo
# counterpart to angullia_send.sh (op#21154/op#22433), sibling to
# nazim_send_photo.sh.
#
# Supervised shape: a reviewer (cc-angullia, per bot_channels.group_routing
# on channel_key='angullia') sends here. Reads ANGULLIA_BOT_TOKEN +
# ANGULLIA_CHAT_ID from .env. NEVER echoes the token.
#
# Usage:
#   scripts/angullia_send_photo.sh <image-path> ["caption"]
#   TG_CHAT_OVERRIDE=<chat_id> scripts/angullia_send_photo.sh <image-path> ["caption"]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^ANGULLIA_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^ANGULLIA_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
IMG="${1:?usage: angullia_send_photo.sh <image-path> [caption]}"
CAP="${2:-}"
[ -n "${TOK:-}" ] || { echo "ANGULLIA_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "ANGULLIA_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }
[ -f "$IMG" ]      || { echo "image not found: $IMG" >&2; exit 1; }

# bus #44576: a real filename can contain ',' or ';', both special to curl's own
# -F parser — route through a syntax-safe staged path instead of @${IMG} directly.
source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"
tg_safe_upload_stage photo "$IMG" || { echo "angullia_send_photo: could not stage upload for $IMG" >&2; exit 1; }
trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT

code=$(curl -s -o /dev/null -w "%{http_code}" \
  -F "chat_id=${CHAT}" -F "$TG_SAFE_UPLOAD_FORM" -F "caption=${CAP}" \
  "https://api.telegram.org/bot${TOK}/sendPhoto" --max-time 30) || code="curl_exit_$?"

# Durable log (tag=angullia) so a rebooted reviewer sees the photo went out.
# CAI-598/600: log DELIVERY, not intent — operator_log defaults delivered=TRUE, so an
# unconditional call records a FAILED send as delivered.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[photo] $(basename "$IMG")${CAP:+ — $CAP}" --chat "$CHAT" --tag angullia \
  $([ "$code" = "200" ] || echo --undelivered) >/dev/null 2>&1 || true

[ "$code" = "200" ] && { echo "sent $(basename "$IMG")"; exit 0; } || { echo "angullia_send_photo failed (status: $code)" >&2; exit 1; }
