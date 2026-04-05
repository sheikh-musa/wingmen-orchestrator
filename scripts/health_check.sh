#!/bin/bash
# Health check — pings all services and alerts via Telegram if anything is down
# Runs every 5 minutes via launchd

set -uo pipefail

source "$HOME/wingmen/orchestrator/.env"
BOT_TOKEN="$(grep TELEGRAM_BOT_TOKEN $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
CHAT_ID="$(grep MUSA_TELEGRAM_ID $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
ALERT_FILE="/tmp/ihsanos_health_alert_sent"

ISSUES=""

# 1. Check ihsanOS web
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://ihsanos.com" 2>/dev/null || echo "000")
if [ "$HTTP" != "200" ] && [ "$HTTP" != "307" ]; then
  ISSUES="$ISSUES\n❌ ihsanos.com: HTTP $HTTP"
fi

# 2. Check Supabase
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
  "https://tscuymavysscrvoberrr.supabase.co/rest/v1/organizations?select=id&limit=1" \
  -H "apikey: $SUPABASE_SERVICE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" 2>/dev/null || echo "000")
if [ "$HTTP" != "200" ]; then
  ISSUES="$ISSUES\n❌ Supabase: HTTP $HTTP"
fi

# 3. Check bot process
if ! pgrep -f "cto_bot.py" > /dev/null 2>&1; then
  ISSUES="$ISSUES\n❌ ihsanOS Bot: not running"
fi

# 4. Check Mizan bot process
if ! pgrep -f "mizan_bot.py" > /dev/null 2>&1; then
  ISSUES="$ISSUES\n❌ Mizan Bot: not running"
fi

# 5. Check brain_sync freshness (should run every 4h)
BRAIN_AGE=$(python3 -c "
import os, time
p = os.path.expanduser('~/wingmen/BRAIN.md')
if os.path.exists(p):
    age = (time.time() - os.path.getmtime(p)) / 3600
    print(f'{age:.1f}')
else:
    print('999')
" 2>/dev/null || echo "999")

if python3 -c "exit(0 if float('$BRAIN_AGE') < 5 else 1)" 2>/dev/null; then
  : # Brain is fresh
else
  ISSUES="$ISSUES\n⚠️ Brain sync stale: ${BRAIN_AGE}h old"
fi

# 6. Check disk space
DISK_USED=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USED" -gt 90 ]; then
  ISSUES="$ISSUES\n⚠️ Disk space: ${DISK_USED}% used"
fi

# Alert if issues found
if [ -n "$ISSUES" ]; then
  # Only alert once per hour (don't spam)
  if [ ! -f "$ALERT_FILE" ] || [ "$(find "$ALERT_FILE" -mmin +60 2>/dev/null)" ]; then
    curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
      -d "chat_id=$CHAT_ID" \
      -d "text=🚨 ihsanOS Health Alert$(echo -e "$ISSUES")" > /dev/null
    touch "$ALERT_FILE"
    echo "$(date): ALERT sent — $ISSUES"
  fi
else
  # Clear alert flag when healthy
  rm -f "$ALERT_FILE"
  echo "$(date): All healthy"
fi
