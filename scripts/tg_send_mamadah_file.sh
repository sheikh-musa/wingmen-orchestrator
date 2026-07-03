#!/usr/bin/env bash
# Send a file to Zahidah via @mamadahbot.
# Usage: tg_send_mamadah_file.sh <chat_id> <filepath> [caption]
set -euo pipefail

CHAT_ID="${1:-}"
FILEPATH="${2:-}"
CAPTION="${3:-}"

if [[ -z "$CHAT_ID" || -z "$FILEPATH" ]]; then
  echo "Usage: tg_send_mamadah_file.sh <chat_id> <filepath> [caption]" >&2
  exit 1
fi

if [[ ! -f "$FILEPATH" ]]; then
  echo "File not found: $FILEPATH" >&2
  exit 1
fi

if [[ -f "$HOME/.wingmen/keys/nutri-science-study-bot.env" ]]; then
  source "$HOME/.wingmen/keys/nutri-science-study-bot.env"
fi
if [[ -z "${NUTRI_STUDY_BOT_TOKEN:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [[ -f "$SCRIPT_DIR/../.env" ]] && source "$SCRIPT_DIR/../.env"
fi

if [[ -z "${NUTRI_STUDY_BOT_TOKEN:-}" ]]; then
  echo "NUTRI_STUDY_BOT_TOKEN not set" >&2
  exit 1
fi

ARGS=(-s -X POST "https://api.telegram.org/bot${NUTRI_STUDY_BOT_TOKEN}/sendDocument"
  -F "chat_id=${CHAT_ID}"
  -F "document=@${FILEPATH}")

if [[ -n "$CAPTION" ]]; then
  ARGS+=(-F "caption=${CAPTION}")
fi

curl "${ARGS[@]}"
