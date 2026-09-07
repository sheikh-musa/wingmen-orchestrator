#!/usr/bin/env bash
# dev_group_send.sh — send a message INTO a dev-group channel as its bot, and log
# the outbound to the substrate. Generic over any `bot_channels` row: resolves the
# channel's token_env_key (from .env) + its allowed_chat_ids[0] (the group), sends
# via that bot, and records an outbound row so the conversation stays durable for
# the future dedicated lane. Interim-manning tool until the per-group agent lanes
# are stood up. NEVER echoes the token.
#
# Usage: scripts/dev_group_send.sh <channel_key> "<text>"
#   e.g. scripts/dev_group_send.sh cosem-exams "Got it — looking into that now."
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
CHANNEL="${1:?usage: dev_group_send.sh <channel_key> \"<text>\"}"
TEXT="${2:?text required}"

# Send via the tested NO-DUPE module (scripts/lib/tg_group_send.py): split connect/read timeouts,
# retry ONLY provably-pre-ack failures, and fail LOUD+actionable on an ambiguous read-timeout rather
# than risk a double-post to the partner group (Nazim 38090/38094). Token stays .env-only, never
# printed. Exit codes: 0 sent-once; 1 not-delivered (safe to re-run); 2 AMBIGUOUS (human must check
# before any resend — the message names exactly what to check).
exec env PYTHONPATH="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/scripts/lib/tg_group_send.py" "$CHANNEL" "$TEXT"
