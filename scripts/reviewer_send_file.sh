#!/usr/bin/env bash
# reviewer_send_file.sh — generic reviewer FILE send into ANY bot_channels group, as
# orch-console (the human-owned reviewer). FILE counterpart of reviewer_send.sh: same
# channel_key lookup (token_env_key + allowed_chat_ids[0] from bot_channels, single
# source of truth) and the same console/irsyad client-comms gate, plus
# cosem_tdu_support_send_photo.sh's caption guards (send_arg_guard,
# client_send_leak_guard), tg_safe_upload staging, and delivery-truthful logging
# (operator_log outbound --tag <channel_key>, --undelivered on failure). NEVER
# echoes the token.
#
# Sends via sendDocument by default; images (.jpg/.jpeg/.png/.gif/.webp) go via
# sendPhoto instead, so they render inline rather than as a downloadable file.
#
# Usage:  scripts/reviewer_send_file.sh <channel_key> <file-path> ["caption"]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
CHANNEL="${1:?usage: reviewer_send_file.sh <channel_key> <file-path> [\"caption\"]}"
FILE="${2:?usage: reviewer_send_file.sh <channel_key> <file-path> [\"caption\"]}"
CAP="${3:-}"
[ -f "$FILE" ] || { echo "reviewer_send_file: file not found: $FILE" >&2; exit 1; }

# Fail-closed: same gate as reviewer_send.sh — the console body (Nazim) may not
# routine-send irsyad CLIENT files; coord owns irsyad client-comms directly. No-op
# for coord/lanes/hub and for every non-irsyad channel this generic tool serves.
source "$ORCH_DIR/scripts/lib/console_irsyad_client_send_gate.sh"
_console_irsyad_client_send_gate "$CHANNEL" || exit 4

# This generic tool can land a file in a CLIENT group same as cosem_tdu_support_send_photo.sh
# does — guard the caption the same way (op#16353 arg-swap, op#21145 internal-identity leak).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$CAP" || exit 2
source "$ORCH_DIR/scripts/lib/client_send_leak_guard.sh"
_client_send_leak_guard "$CAP" || exit 3

# Resolve token_env_key + chat_id from bot_channels (single source of truth), via
# scripts/bus_send.dburl (PR#214/#219): the .env FILE wins over the inherited env, so
# a long-running caller that's still holding a pre-rotation DATABASE_URL doesn't fail
# auth and feed the pooler circuit breaker (2026-09-28 rotation incident; hit again
# here 2026-09-29 before this fix). nazim_bus_notify._dsn() has the opposite order
# (inherited-env-first) and is deliberately left alone — other callers, fleet-health
# owns that fleet-wide sweep.
read -r TOKEN_KEY CHAT < <(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$CHANNEL" <<'PY'
import sys, os, psycopg
from scripts.bus_send import dburl
ch = sys.argv[1]
with psycopg.connect(dburl(dict(os.environ))) as c:
    r = c.execute("SELECT token_env_key, allowed_chat_ids FROM bot_channels WHERE channel_key=%s", (ch,)).fetchone()
if not r or not r[0] or not r[1]:
    sys.exit(2)
print(r[0], r[1][0])
PY
) || { echo "reviewer_send_file: channel '$CHANNEL' has no token_env_key/allowed_chat_ids in bot_channels" >&2; exit 2; }

TOK=$(grep -m1 "^${TOKEN_KEY}=" "$ORCH_DIR/.env" | cut -d= -f2-)
[ -n "${TOK:-}" ] || { echo "reviewer_send_file: ${TOKEN_KEY} missing from .env" >&2; exit 1; }

# bus #44576: a real filename can contain ',' or ';', both special to curl's own
# -F parser — route through a syntax-safe staged path instead of @${FILE} directly.
source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"

# Images render inline via sendPhoto; everything else (PDFs, docs, zips…) via sendDocument.
shopt -s nocasematch
case "$FILE" in
  *.jpg|*.jpeg|*.png|*.gif|*.webp) METHOD=sendPhoto ;;
  *)                                METHOD=sendDocument ;;
esac
shopt -u nocasematch

if [ "$METHOD" = sendPhoto ]; then
  tg_safe_upload_stage photo "$FILE" || { echo "reviewer_send_file: could not stage upload for $FILE" >&2; exit 1; }
else
  tg_safe_upload_stage document "$FILE" || { echo "reviewer_send_file: could not stage upload for $FILE" >&2; exit 1; }
fi
trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT

# --form-string (not -F) for chat_id/caption: -F treats a value starting with
# @ or < as a file to read, so a caption like "@Rhaihan" or "<3" would try to
# upload/read a local file instead of being sent as literal text.
code=$(curl -s -o /dev/null -w "%{http_code}" \
  --form-string "chat_id=${CHAT}" -F "$TG_SAFE_UPLOAD_FORM" --form-string "caption=${CAP}" \
  "https://api.telegram.org/bot${TOK}/${METHOD}" --max-time 60) || code="curl_exit_$?"

# Durable log — CAI-598: record delivery, not intent.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "[file] $(basename "$FILE")${CAP:+ — $CAP}" --chat "$CHAT" --tag "$CHANNEL" \
  $([ "$code" = "200" ] || echo --undelivered) >/dev/null 2>&1 || true

[ "$code" = "200" ] && { echo "sent $(basename "$FILE") to $CHANNEL"; exit 0; } || { echo "reviewer_send_file to '$CHANNEL' failed (status: $code)" >&2; exit 1; }
