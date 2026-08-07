#!/usr/bin/env bash
# caai_send_file.sh — send a FILE (HTML console, PDF, report…) into the CAAI
# (Ray / Syed SME) group via the COSEM-CAAI bot. The file counterpart to
# caai_send.sh (text). This is how orch-console (Nazim, the human-owned reviewer
# for cc-caai) delivers reviewed documents to Ray directly.
#
# The cosem-caai channel is agent_phase='supervised': the caai LANE files drafts
# to its reviewer (orch-console), who reviews + sends. This is that reviewer file
# path — it does NOT go through the lane gate.
#
# Mirrors irsyad_support_send_file.sh (proven perimeter file-send) for the
# sendDocument shape, and caai_send.sh for the token + authoritative chat_id
# (bot_channels, single source of truth) + CAI-598 delivery-logging. NEVER echoes
# the token.
# Usage:
#   scripts/caai_send_file.sh <filepath> [caption]
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^COSEM_CAAI_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
[ -n "${TOK:-}" ] || { echo "COSEM_CAAI_BOT_TOKEN missing from .env" >&2; exit 1; }

# chat_id from bot_channels.allowed_chat_ids (first element) — authoritative,
# same source as caai_send.sh; avoids a drifting second .env copy.
CHAT=$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - <<'PY'
import psycopg
from nervous_system.nazim_bus_notify import _dsn
with psycopg.connect(_dsn()) as c:
    r = c.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='cosem-caai'").fetchone()
print(r[0][0] if r and r[0] else "")
PY
)
[ -n "${CHAT:-}" ] || { echo "cosem-caai has no allowed_chat_ids in bot_channels" >&2; exit 1; }

FILE="${1:?usage: caai_send_file.sh <filepath> [caption]}"
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
# CAI-598: log DELIVERY, not intent — operator_log defaults delivered=TRUE.
NOTE="sent a FILE → $(basename "$FILE")$([ -n "$CAPTION" ] && echo "  | caption: $CAPTION")"
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$NOTE" --chat "$CHAT" --tag cosem-caai $([ "$ok" = "True" ] || echo --undelivered) >/dev/null 2>&1 || true

if [ "$ok" = "True" ]; then
  echo "sent: $(basename "$FILE") -> $CHAT"
  exit 0
else
  echo "caai_send_file failed: $resp" >&2
  exit 1
fi
