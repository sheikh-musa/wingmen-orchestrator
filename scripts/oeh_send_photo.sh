#!/usr/bin/env bash
# oeh_send_photo.sh — send a PHOTO to the OEH client channel, photo
# counterpart to oeh_send.sh, sibling to angullia_send_photo.sh/nazim_send_photo.sh.
#
# Supervised shape: a reviewer (cc-oeh, per bot_channels.group_routing on
# channel_key='oeh') sends here. Reads OEH_BOT_TOKEN + OEH_CHAT_ID from .env.
# NEVER echoes the token.
#
# Usage:
#   scripts/oeh_send_photo.sh <image-path> ["caption"]
#   TG_CHAT_OVERRIDE=<chat_id> scripts/oeh_send_photo.sh <image-path> ["caption"]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^OEH_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^OEH_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
IMG="${1:?usage: oeh_send_photo.sh <image-path> [caption]}"
CAP="${2:-}"
[ -n "${TOK:-}" ] || { echo "OEH_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "OEH_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }
[ -f "$IMG" ]      || { echo "image not found: $IMG" >&2; exit 1; }

# This is a CLIENT channel (oeh) — the caption gets the same guards
# oeh_send.sh runs on its text, so a caption can't bypass them.
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$CAP" || exit 2
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$CAP" || exit 3
# orch-console #57970/#58047: refuse a weekday paired with the wrong date in the caption. Fail-closed.
source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
_date_weekday_guard "$CAP" || exit 6

source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"
tg_safe_upload_stage photo "$IMG" || { echo "oeh_send_photo: could not stage upload for $IMG" >&2; exit 1; }
trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT

code=$(curl -s -o /dev/null -w "%{http_code}" \
  --form-string "chat_id=${CHAT}" -F "$TG_SAFE_UPLOAD_FORM" --form-string "caption=${CAP}" \
  "https://api.telegram.org/bot${TOK}/sendPhoto" --max-time 30) || code="curl_exit_$?"

PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[photo] $(basename "$IMG")${CAP:+ — $CAP}" --chat "$CHAT" --tag oeh \
  $([ "$code" = "200" ] || echo --undelivered) >/dev/null 2>&1 || true

[ "$code" = "200" ] && { echo "sent $(basename "$IMG")"; exit 0; } || { echo "oeh_send_photo failed (status: $code)" >&2; exit 1; }
