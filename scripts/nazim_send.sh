#!/usr/bin/env bash
# nazim_send.sh — Nazim (console/CTO body) → operator via @nazim_cto_bot.
#
# This is the console body's OWN Telegram voice: a DISTINCT bot identity
# (NAZIM_BOT_TOKEN) on a DISTINCT channel (nazim-console) — NEVER @wingmennorchbot.
# It therefore does NOT pass through the ORCH-TOPOLOGY-001 pen-(iv) gate: that
# gate protects the HUB's operator-orch channel, which this script structurally
# cannot touch (token + channel are hard-wired to Nazim's own). Identity
# separation by token+channel is exactly the CAI-RESP-389 design. The gate on
# tg_send.sh still fail-closes the console body out of the hub's channel — this
# is the sanctioned counterpart that lets Nazim speak as himself.
#
# Reads NAZIM_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:  scripts/nazim_send.sh "message text"
#         echo "message" | scripts/nazim_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^NAZIM_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "NAZIM_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

TEXT="${1:-$(cat)}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

# Send (chunked at Telegram's 4096-char limit). token/chat/text via env, not argv.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi

# Durable log every reply (tag=nazim-console → scopes to the console body and
# keeps the two-way thread coherent for a rebooted Nazim). Best-effort.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
  outbound "$TEXT" --chat "$CHAT" --tag nazim-console >/dev/null 2>&1 || true

[ "$sent" = 1 ] && exit 0 || { echo "nazim_send failed" >&2; exit 1; }
