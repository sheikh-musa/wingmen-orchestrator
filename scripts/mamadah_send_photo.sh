#!/usr/bin/env bash
# mamadah_send_photo.sh — send a PHOTO to Mama Dah's Assistant's private
# family group, photo counterpart to mamadah_send.sh, sibling to
# oeh_send_photo.sh/angullia_send_photo.sh/nazim_send_photo.sh.
#
# Supervised shape: a reviewer (cc-mamadah, per bot_channels.group_routing
# on channel_key='mamadah') sends here. Reads MAMADAH_BOT_TOKEN/
# MAMADAH_CHAT_ID from .env. NEVER echoes the token.
#
# This is PRIVATE FAMILY data (Musa + wife Zahidah), not a client
# workstream, but the same anti-leak / arg-swap guards apply as
# mamadah_send.sh runs on its text — a caption can't bypass them.
#
# Usage:
#   scripts/mamadah_send_photo.sh <image-path> ["caption"]
#   TG_CHAT_OVERRIDE=<chat_id> scripts/mamadah_send_photo.sh <image-path> ["caption"]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^MAMADAH_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MAMADAH_CHAT_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
IMG="${1:?usage: mamadah_send_photo.sh <image-path> [caption]}"
CAP="${2:-}"
[ -n "${TOK:-}" ] || { echo "MAMADAH_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MAMADAH_CHAT_ID missing from .env (and no TG_CHAT_OVERRIDE)" >&2; exit 1; }
[ -f "$IMG" ]      || { echo "image not found: $IMG" >&2; exit 1; }

# wingmen-personal credential for operator_log's personal-routing split-write
# (bus #47837 C3) — GUARDED file, deliberately NOT in .env. Scoped to this
# outbound-send process only, not exported fleet-wide. Same as mamadah_send.sh.
export WINGMEN_PERSONAL_ALLOWED=1
. "$HOME/.wingmen/private/wingmen_personal.env"

source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$CAP" || exit 2
_send_tag_shape_guard "mamadah" || exit 2

# No internal identity / escalation framing reaches the family group (same
# guard as mamadah_send.sh / every other *_send.sh sibling, op#21145).
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$CAP" || exit 3
# orch-console #57970/#58089/#58243: refuse a weekday paired with the wrong date in the
# caption. A confirmed mismatch refuses (exit 6); a guard crash sends anyway and pages.
source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
_date_weekday_guard "$CAP" || exit 6

# Scrub secret PATTERNS from the caption before it leaves the process — same
# baseline pass mamadah_send.sh runs on its text. Clean input round-trips
# byte-identical; a redactor hiccup must not drop the photo, so fall back to
# the original caption on failure.
if [ -n "$CAP" ]; then
  if REDACTED="$(printf '%s' "$CAP" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
    CAP="$REDACTED"
  fi
fi

source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"
tg_safe_upload_stage photo "$IMG" || { echo "mamadah_send_photo: could not stage upload for $IMG" >&2; exit 1; }
trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT

code=$(curl -s -o /dev/null -w "%{http_code}" \
  --form-string "chat_id=${CHAT}" -F "$TG_SAFE_UPLOAD_FORM" --form-string "caption=${CAP}" \
  "https://api.telegram.org/bot${TOK}/sendPhoto" --max-time 30) || code="curl_exit_$?"

PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[photo] $(basename "$IMG")${CAP:+ — $CAP}" --chat "$CHAT" --tag mamadah \
  $([ "$code" = "200" ] || echo --undelivered) >/dev/null 2>&1 || true

[ "$code" = "200" ] && { echo "sent $(basename "$IMG")"; exit 0; } || { echo "mamadah_send_photo failed (status: $code)" >&2; exit 1; }
