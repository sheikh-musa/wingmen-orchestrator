#!/usr/bin/env bash
# caai_send.sh — reviewer send into the CAAI (Ray / Syed SME) group via the
# COSEM-CAAI bot. This is how orch-console (Nazim, the human-owned reviewer for
# cc-caai) speaks to Ray directly — status updates and approved drafts.
#
# The cosem-caai channel is agent_phase='supervised': the caai LANE files drafts
# to its reviewer, who sends them. This is that reviewer send path — it does NOT
# go through the lane gate. (Operator-confirmed 2026-07-27 op#7689: "you have
# direct access to the channel and have been communicating with him there.")
#
# Mirrors irsyad_support_send.sh (proven perimeter-send pattern), incl. CAI-598
# delivery-logging (log DELIVERY not intent — a failed send must not log as sent).
# Token from .env; chat_id is the single source of truth in bot_channels. NEVER
# echoes the token.
# Usage:
#   scripts/caai_send.sh "message text"
#   echo "message" | scripts/caai_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^COSEM_CAAI_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
[ -n "${TOK:-}" ] || { echo "COSEM_CAAI_BOT_TOKEN missing from .env" >&2; exit 1; }

# chat_id from bot_channels.allowed_chat_ids[1] — authoritative, avoids a drifting
# second .env copy.
CHAT=$(PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - <<'PY'
import psycopg
from nervous_system.nazim_bus_notify import _dsn
with psycopg.connect(_dsn()) as c:
    r = c.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='cosem-caai'").fetchone()
print(r[0][0] if r and r[0] else "")
PY
)
[ -n "${CHAT:-}" ] || { echo "cosem-caai has no allowed_chat_ids in bot_channels" >&2; exit 1; }

TEXT="${1:-$(cat)}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

# Send (chunked at Telegram's 4096-char limit). token/chat/text via env, never argv.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# Durable log — CAI-598: record delivery, not intent. PYTHONPATH pins the package root.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" --tag cosem-caai $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "caai_send failed" >&2; exit 1; }
