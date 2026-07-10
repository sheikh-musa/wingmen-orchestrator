#!/bin/bash
# Health check — pings all services and alerts via Telegram if anything is down
# Runs every 5 minutes via launchd

set -uo pipefail

source "$HOME/wingmen/orchestrator/.env"
BOT_TOKEN="$(grep "^WINGMEN_BOT_TOKEN=" $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
CHAT_ID="$(grep "^MUSA_TELEGRAM_ID=" $HOME/wingmen/orchestrator/.env | cut -d= -f2)"
ALERT_FILE="/tmp/ihsanos_health_alert_sent"

ISSUES=""

# Ping a URL, return clean HTTP code (000 = connection failed, no appended junk).
#
# HTTP 000 is a CONNECTION-level failure (DNS/edge/network hiccup from THIS probe
# host) — almost always transient, NOT a real Supabase outage (a down project
# returns 5xx, or sustained 000). A single retry (the old logic) wasn't enough:
# a blip surviving two attempts 2s apart still paged. That is the exact class
# that retired the ihsanos.com ping (2026-07-06) and re-fired on the Supabase
# probe (operator, 2026-07-10). So: a REAL http status (incl. 4xx/5xx) returns
# immediately and pages as before; only 000 gets retried — up to 4 attempts with
# growing backoff (~12s) — and we report 000 ONLY if EVERY attempt fails (a
# genuine sustained outage). Kills single-blip false-pages, keeps real-outage
# detection.
ping_http() {
  local url="$1"; shift
  local http backoff
  for backoff in 0 2 4 6; do
    [ "$backoff" -gt 0 ] && sleep "$backoff"
    http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$@" "$url" 2>/dev/null)
    [ -n "$http" ] && [ "$http" != "000" ] && { echo "$http"; return; }
  done
  echo "000"
}

# 1. (RETIRED 2026-07-06) ihsanOS web (https://ihsanos.com) uptime ping.
#    Removed per operator — it false-paged on transient HTTP 000 blips (a single
#    DNS/edge hiccup surviving the one retry), while the site was healthy (200).
#    The bare marketing domain is not the critical path; actual data-layer health
#    is covered by the Supabase probes (#2, #3) below. Can be re-added as a
#    consecutive-failure check (page only after N straight fails) if uptime
#    monitoring is wanted without the blip noise.

# 2. Orchestrator Supabase (tscuymavysscrvoberrr = wingmen-ops).
#    Probe `agents` (core substrate table, never dropped). Was bot_heartbeat,
#    which the 2026-07-01 monolith cleanup dropped with the bot_* test tables —
#    the probe then 404'd forever and paged the operator hourly about a healthy
#    DB (diagnosed by cai 2026-07-02).
HTTP=$(ping_http \
  "https://tscuymavysscrvoberrr.supabase.co/rest/v1/agents?select=id&limit=1" \
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

# 3. (retired) ihsanOS bot — cto_bot.py is RETIRED, replaced by @dookanabot
#    (env token is IHSANOSBOT_TOKEN_RETIRED; launchd plist moved to
#    ops/launchd/disabled-ihsanosbot/). No live process to monitor, so this
#    check was a permanent false "not running" alert — removed (OPS-HEALTH-338 #1).

# 4. (suspended) Mizan bot process — dark since the Mac Mini cutover; alerting
#    on a bot we KNOW is down pages the operator hourly with no action to take.
#    Revives as an ai-responder channel in bot-ingest P3 (CAI-RESP-357); at P4
#    the watchdog reads bot_channels.enabled instead of pgrep, and this whole
#    check class is deleted. Suspended by cai 2026-07-02.
# if ! pgrep -f "mizan_bot.py" > /dev/null 2>&1; then
#   ISSUES="$ISSUES\n❌ Mizan Bot: not running"
# fi

# 5. (retired) Brain-sync freshness — wingmen_brain/BRAIN.md sync is DEPRECATED,
#    superseded by the Supabase substrate. Its only writer was cto_bot (retired),
#    so the file always went stale and this check fired permanently. cai
#    confirmed RETIRE (OPS-HEALTH-338 #4 / item C).

# 6. Check disk space
DISK_USED=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USED" -gt 90 ]; then
  ISSUES="$ISSUES\n⚠️ Disk space: ${DISK_USED}% used"
fi

# 7. CI health — detect a GitHub Actions startup_failure outage (CAI-RESP-338).
#    The billing API is 404 to our token, so we detect the FAILURE SIGNAL instead:
#    if the latest 3 ihsanos runs ALL conclude startup_failure (0s, runner never
#    starts), Actions is blocked — almost always the monthly minutes/budget cap,
#    the exact outage of 2026-06-28 that was discovered only via a broken merge.
#    Alerts the WAR ROOM ONCE per outage (own dedup, cleared on recovery), so it
#    never again surfaces through a failed merge. Independent of the DM alert above.
CI_ALERT_FILE="/tmp/ci_down_alert_sent"
GH_BIN="$(command -v gh || echo /usr/local/bin/gh)"
if [ -x "$GH_BIN" ] && [ -n "${GH_TOKEN:-}" ]; then
  CI_CONCS=$("$GH_BIN" run list -R sheikh-musa/ihsanos --limit 3 --json conclusion -q '.[].conclusion' 2>/dev/null)
  CI_TOTAL=$(printf '%s\n' "$CI_CONCS" | grep -c .)
  CI_FAILS=$(printf '%s\n' "$CI_CONCS" | grep -c 'startup_failure')
  if [ "$CI_TOTAL" -ge 3 ] && [ "$CI_FAILS" = "$CI_TOTAL" ]; then
    if [ ! -f "$CI_ALERT_FILE" ]; then
      TG_CHAT_OVERRIDE="-5383530504" "$HOME/wingmen/orchestrator/scripts/tg_send.sh" \
        "🚨 CI DOWN — GitHub Actions is failing at startup on every run (latest $CI_TOTAL all died at 0s). Most likely the monthly Actions minutes/budget is exhausted, so merges will block. Fix: raise the GitHub Actions spending limit (or wait for the next billing cycle reset). This is NOT a code bug." >/dev/null 2>&1
      touch "$CI_ALERT_FILE"
      echo "$(date): CI-DOWN alert sent to war-room ($CI_FAILS/$CI_TOTAL startup_failure)"
    fi
  else
    # CI recovered (a non-failing run appeared) — clear the dedup so a future outage re-alerts
    if [ -f "$CI_ALERT_FILE" ]; then
      rm -f "$CI_ALERT_FILE"
      echo "$(date): CI recovered — alert flag cleared"
    fi
  fi
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
