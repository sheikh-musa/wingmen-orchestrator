#!/bin/bash
# Daily Supabase backup — dumps all tables to JSON files
# Runs via launchd at 3 AM SGT
# Keeps last 7 days of backups

set -euo pipefail

BACKUP_DIR="$HOME/wingmen/backups"
DATE=$(date +%Y-%m-%d)
TODAY_DIR="$BACKUP_DIR/$DATE"
SUPABASE_URL="https://tscuymavysscrvoberrr.supabase.co"
source "$HOME/wingmen/orchestrator/.env"
KEY="$SUPABASE_SERVICE_KEY"
BOT_TOKEN="$(grep TELEGRAM_BOT_TOKEN $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
CHAT_ID="$(grep MUSA_TELEGRAM_ID $HOME/wingmen/orchestrator/.env | cut -d= -f2)"

mkdir -p "$TODAY_DIR"

echo "=== Supabase Backup — $DATE ==="

# Tables to back up
TABLES=(
  "organizations"
  "clients"
  "client_repos"
  "jobs"
  "chat_history"
  "wingmen_brain"
  "brain_sync_log"
  "usage_log"
  "plan_tiers"
  "audit_log"
  "message_queue"
  "donations"
  "donation_categories"
  "receipts"
  "persons"
  "person_roles"
  "org_members"
  "profiles"
  "pos_products"
  "pos_sessions"
  "pos_transactions"
  "pos_transaction_items"
  "ayat"
  "tafsir_entries"
  "topics"
  "asbab_nuzul"
)

FAILED=0
BACKED=0

for TABLE in "${TABLES[@]}"; do
  echo -n "  $TABLE... "
  HTTP_CODE=$(curl -s -o "$TODAY_DIR/$TABLE.json" -w "%{http_code}" \
    "$SUPABASE_URL/rest/v1/$TABLE?select=*" \
    -H "apikey: $KEY" \
    -H "Authorization: Bearer $KEY" \
    -H "Range: 0-99999")

  if [ "$HTTP_CODE" = "200" ]; then
    SIZE=$(wc -c < "$TODAY_DIR/$TABLE.json" | tr -d ' ')
    ROWS=$(python3 -c "import json; print(len(json.load(open('$TODAY_DIR/$TABLE.json'))))" 2>/dev/null || echo "?")
    echo "✓ $ROWS rows ($SIZE bytes)"
    BACKED=$((BACKED + 1))
  else
    echo "✗ HTTP $HTTP_CODE"
    FAILED=$((FAILED + 1))
  fi
done

# Cleanup old backups (keep 7 days)
find "$BACKUP_DIR" -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \;

echo ""
echo "=== Done: $BACKED tables backed up, $FAILED failed ==="
echo "Location: $TODAY_DIR"

# Alert on failure
if [ "$FAILED" -gt 0 ]; then
  curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
    -d "chat_id=$CHAT_ID" \
    -d "text=⚠️ Daily backup had $FAILED failures. Check $TODAY_DIR" > /dev/null
fi
