#!/usr/bin/env bash
# tg_send.sh — send a message to the operator via the Wingmen Orchestrator bot
# (@wingmennorchbot). The outbound half of the 2-way bridge: cc-orchestrator
# (and the daily brief) call this to reach the operator's phone.
#
# Reads WINGMEN_BOT_TOKEN + MUSA_TELEGRAM_ID from .env. NEVER echoes the token.
# Usage:
#   scripts/tg_send.sh "message text"
#   echo "message" | scripts/tg_send.sh
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
TOK=$(grep '^WINGMEN_BOT_TOKEN=' "$ORCH_DIR/.env" | cut -d= -f2-)
# Default target = the operator (Musa). TG_CHAT_OVERRIDE lets the bridge route a
# reply to a different authorized chat (e.g. the shipforge/storefront group).
CHAT="${TG_CHAT_OVERRIDE:-$(grep '^MUSA_TELEGRAM_ID=' "$ORCH_DIR/.env" | cut -d= -f2-)}"
[ -n "${TOK:-}" ] || { echo "WINGMEN_BOT_TOKEN missing from .env" >&2; exit 1; }
[ -n "${CHAT:-}" ] || { echo "MUSA_TELEGRAM_ID missing from .env" >&2; exit 1; }

# ORCH-TOPOLOGY-001 pen (iv): only the lease-holder hub speaks on this channel.
# Fail-closed for the console body (Nazim); fail-safe for the hub (never strand it).
if ! GATE=$("$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/orch_lease.py" check); then
  echo "tg_send REFUSED — $GATE" >&2
  mkdir -p "$ORCH_DIR/logs" 2>/dev/null || true
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') tg_send REFUSED (pen iv) :: ${1:-<stdin>}" >> "$ORCH_DIR/logs/pen_gate.log" 2>/dev/null || true
  exit 3
fi

TEXT="${1:-$(cat)}"
TAG="${2:-}"   # optional @alias context this reply pertains to
[ -n "$TEXT" ] || { echo "no text to send" >&2; exit 1; }
# Fail loud on the channel-first arg-swap footgun (op#16353).
source "$ORCH_DIR/scripts/lib/send_arg_guard.sh"
_send_arg_guard "$TEXT" || exit 2

# Scrub secret patterns (pg DSNs, bot tokens, API keys) BEFORE anything leaves
# the process — the send AND the durable log both use the scrubbed text. Clean
# input round-trips byte-identical; a redactor hiccup must not drop the message,
# so fall back to the original text on failure.
if REDACTED="$(printf '%s' "$TEXT" | PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.secret_redact 2>/dev/null)"; then
  TEXT="$REDACTED"
fi

# Send (chunked at Telegram's 4096-char limit so long replies aren't truncated).
# token/chat/text passed via env, never argv — keeps the token out of `ps`.
if TG_TOK="$TOK" TG_CHAT="$CHAT" TG_TEXT="$TEXT" \
     "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/_tg_chunked_send.py"; then
  sent=1
else
  sent=0
fi
# durable log every reply (full text, once; best-effort — never fail on a log hiccup).
# PYTHONPATH pins the package root so the `-m` import works regardless of CWD (a
# bare `-m nervous_system.operator_log` only resolves when run from $ORCH_DIR;
# any other caller-CWD would ModuleNotFoundError and silently drop the log).
# CAI-598: log DELIVERY, not intent. This log line used to run unconditionally with delivered
# defaulting to TRUE, so a failed send was recorded as delivered. Ported from the Studio's fix
# (25701aa) — which had been applied there and NOT here: the fix was host-split, each machine
# carrying half of it, for the same two-host reason that has bitten five times today.
PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" -m nervous_system.operator_log outbound "$TEXT" --chat "$CHAT" ${TAG:+--tag "$TAG"} $([ "$sent" = 1 ] || echo --undelivered) >/dev/null 2>&1 || true
[ "$sent" = 1 ] && exit 0 || { echo "tg_send failed" >&2; exit 1; }
