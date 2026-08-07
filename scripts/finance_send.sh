#!/usr/bin/env bash
# finance_send.sh — cc-finance (head-of-revenue body) → operator via @wingmen_revenue_bot.
#
# The finance agent's OWN Telegram voice: a DISTINCT bot identity (FINANCE_BOT_TOKEN)
# on a DISTINCT channel (finance-console) — never @wingmennorchbot, never @nazim_cto_bot.
# Identity separation by token+channel, exactly like nazim_send.sh (CAI-RESP-389 shape):
# it structurally cannot touch the hub's or Nazim's channels. Provisioned 2026-08-02
# (op#8864/9086) for payment screenshots + client-pricing.
#
# Reads FINANCE_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:  scripts/finance_send.sh "message text"
#         echo "message" | scripts/finance_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^FINANCE_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
CHAT="${TG_CHAT_OVERRIDE:-${MUSA_TELEGRAM_ID:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}}"
[ -n "${TOK:-}" ] || { echo "FINANCE_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

TEXT="${1:-$(cat)}"
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }

# Scrub secret patterns BEFORE anything leaves the process (send AND durable log
# both use the scrubbed text). Clean input round-trips byte-identical; a redactor
# hiccup must not drop the message, so fall back to the original on failure.
if REDACTED="$(printf '%s' "$TEXT" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
  TEXT="$REDACTED"
fi

# Send (chunked at Telegram's 4096-char limit). token/chat/text via env, not argv.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi

# Durable log every reply (tag=finance-console → scopes to the finance body and
# keeps the two-way thread coherent). `delivered` reflects what actually happened.
if [ "$sent" = 1 ]; then
  PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
    outbound "$TEXT" --chat "$CHAT" --tag finance-console >/dev/null 2>&1 || true
else
  PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log \
    outbound "$TEXT" --chat "$CHAT" --tag finance-console --undelivered >/dev/null 2>&1 || true
fi

[ "$sent" = 1 ] && exit 0 || { echo "finance_send failed" >&2; exit 1; }
