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

# ORCH-TOPOLOGY-001 pen (iv): only the lease-holder hub speaks on this channel.
if ! GATE=$("$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/orch_lease.py" check); then
  echo "tg_send_file REFUSED — $GATE" >&2
  mkdir -p "$ORCH_DIR/logs" 2>/dev/null || true
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') tg_send_file REFUSED (pen iv) :: ${1:-}" >> "$ORCH_DIR/logs/pen_gate.log" 2>/dev/null || true
  exit 3
fi

FILE="${1:?usage: tg_send_file.sh <filepath> [caption]}"
CAPTION="${2:-}"
[ -f "$FILE" ] || { echo "file not found: $FILE" >&2; exit 1; }

# Size guard (Telegram bot cap is 50MB)
bytes=$(stat -f%z "$FILE" 2>/dev/null || stat -c%s "$FILE" 2>/dev/null || echo 0)
if [ "$bytes" -gt 52428800 ]; then
  echo "file too large for Telegram (${bytes} bytes > 50MB)" >&2; exit 1
fi

# bus #44576: a real filename can contain ',' or ';', both special to curl's own
# -F parser — route through a syntax-safe staged path instead of @${FILE} directly.
# orch-console #57970/#58047: refuse a weekday paired with the wrong date in the caption. Fail-closed.
source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"
_date_weekday_guard "$CAPTION" || exit 6
# fable audit 2026-10-06 D1/D2: refuse a rendered file carrying a placeholder leak
# or fleet-internal vocabulary. Fail-closed on a confirmed hit, fail-open on a crash.
source "$ORCH_DIR/scripts/lib/client_artifact_scan.sh"
_client_artifact_scan "$FILE" || exit 7

source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"
tg_safe_upload_stage document "$FILE" || { echo "tg_send_file: could not stage upload for $FILE" >&2; exit 1; }
trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT

# bus #44680: -F treats a value starting with '@' (upload) or '<' (read-file) as a
# file directive, not literal text — a caption/chat_id beginning with either would
# either fail or leak a local file's contents. --form-string sends them as literal.
resp=$(curl -s --ipv4 "https://api.telegram.org/bot${TOK}/sendDocument" \
  --form-string "chat_id=${CHAT}" \
  -F "$TG_SAFE_UPLOAD_FORM" \
  ${CAPTION:+--form-string "caption=${CAPTION}"}) || resp="{\"ok\":false,\"description\":\"curl_exit_$?\"}"
ok=$(printf '%s' "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('ok') else '0:'+str(d.get('description')))")

# durable log (best-effort)
# CAI-598/600: log DELIVERY, not intent — operator_log defaults delivered=TRUE, so an
# unconditional call records a FAILED send as delivered.
# PYTHONPATH pins the package root so the `-m` import resolves regardless of CWD. A bare
# `-m nervous_system.operator_log` only resolves when run from $ORCH_DIR; any other
# caller-CWD ModuleNotFounds and (behind `|| true`) SILENTLY drops the delivery log.
# Mirrors the sibling tg_send.sh:59 fix (07-01 audit #11; host-split, this leg never patched).
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound \
  "[file] $(basename "$FILE")${CAPTION:+ — $CAPTION}" --chat "$CHAT" \
  $([ "$ok" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true

case "$ok" in
  1) exit 0 ;;
  *) echo "tg_send_file error: ${ok#0:}" >&2; exit 1 ;;
esac
