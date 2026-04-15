#!/bin/bash
# Health check — pings all services and alerts via Telegram if anything is down
# Runs every 5 minutes via launchd

set -uo pipefail

source "$HOME/wingmen/orchestrator/.env"
BOT_TOKEN="$(grep TELEGRAM_BOT_TOKEN $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
CHAT_ID="$(grep MUSA_TELEGRAM_ID $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
ALERT_FILE="/tmp/ihsanos_health_alert_sent"

ISSUES=""

# Ping a URL, return clean HTTP code (000 = connection failed, no appended junk).
# Retries once with a short backoff so a single DNS blip doesn't page Musa.
ping_http() {
  local url="$1"; shift
  local http
  http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$@" "$url" 2>/dev/null)
  if [ -z "$http" ] || [ "$http" = "000" ]; then
    sleep 2
    http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$@" "$url" 2>/dev/null)
    [ -z "$http" ] && http="000"
  fi
  echo "$http"
}

# 1. ihsanOS web
HTTP=$(ping_http "https://ihsanos.com")
if [ "$HTTP" != "200" ] && [ "$HTTP" != "307" ]; then
  ISSUES="$ISSUES\n❌ ihsanos.com: HTTP $HTTP"
fi

# 2. Orchestrator Supabase (tscuymavysscrvoberrr = wingmen-ops).
#    Query bot_heartbeat (orchestrator-owned) not organizations (ihsanos-owned
#    and about to be dropped in Phase C).
HTTP=$(ping_http \
  "https://tscuymavysscrvoberrr.supabase.co/rest/v1/bot_heartbeat?select=service&limit=1" \
  -H "apikey: $SUPABASE_SERVICE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_KEY")
if [ "$HTTP" != "200" ]; then
  ISSUES="$ISSUES\n❌ Orchestrator Supabase: HTTP $HTTP"
fi

# 3. ihsanOS prod Supabase (ceayjeamtmcyzzvqflus = new Singapore project).
#    Query organizations (the canonical ihsanos table post-migration).
if [ -n "${IHSANOS_SUPABASE_SERVICE_KEY:-}" ]; then
  HTTP=$(ping_http \
    "https://ceayjeamtmcyzzvqflus.supabase.co/rest/v1/organizations?select=id&limit=1" \
    -H "apikey: $IHSANOS_SUPABASE_SERVICE_KEY" \
    -H "Authorization: Bearer $IHSANOS_SUPABASE_SERVICE_KEY")
  if [ "$HTTP" != "200" ]; then
    ISSUES="$ISSUES\n❌ ihsanOS Supabase: HTTP $HTTP"
  fi
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
