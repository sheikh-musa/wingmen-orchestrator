#!/usr/bin/env bash
# disk_space_monitor.sh — early-warning for the Mac Mini's DATA volume filling up.
#
# WHY: on 2026-08-21 /System/Volumes/Data hit 100% full, which crashed the SRE
# liveness watchdog (and any process trying to write) with [Errno 28] No space
# left on device. The pre-existing health_check.sh disk check was (a) DEAD (its
# launchd job unloaded since ~2026-07-02) and (b) watched the WRONG volume —
# `df -h /` reports the read-only SYSTEM volume (~92% static), never the DATA
# volume that actually fills. So there was effectively no disk early-warning.
# Operator (op#15550) asked us to "keep track of the mini's resources" — this is
# that mechanism, built in code, not a promise to remember.
#
# WHAT: checks the DATA volume every run.
#   - WARN  (>= WARN_PCT, default 85): nudge orch-console on the bus so NAZIM
#           clears regenerable space (npm/.next/caches) BEFORE it's critical.
#   - CRIT  (>= CRIT_PCT, default 93): bus nudge + a direct ELI5 Telegram alert
#           to the operator (nazim_send.sh) — at this point action is urgent.
#   - Healthy (< WARN_PCT): clears the dedup flags.
# Dedup: one alert per level per hour (flag files in /tmp). If /tmp itself is on
# the full volume the flag write may fail — acceptable: worst case a duplicate
# CRIT alert, which for a full disk is the right failure direction.
#
# Scoped to the console body: it writes an orch-console bus row + (crit only)
# the operator's nazim-console voice. It NEVER touches lanes or the hub pen.
set -uo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Baseline sits ~86% from legitimate worktree data, so WARN must clear it.
# Lower these (env override) after a worktree dedup brings the baseline down.
WARN_PCT="${DISK_WARN_PCT:-89}"
CRIT_PCT="${DISK_CRIT_PCT:-94}"
DATA_VOL="${DISK_DATA_VOL:-/System/Volumes/Data}"
WARN_FLAG="/tmp/disk_monitor_warn_sent"
CRIT_FLAG="/tmp/disk_monitor_crit_sent"
LOG="$ORCH_DIR/logs/disk_space_monitor.log"

# --- read the DATA volume usage (the one that actually fills) ---
USED_PCT=$(df -k "$DATA_VOL" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
AVAIL_H=$(df -h "$DATA_VOL" 2>/dev/null | awk 'NR==2{print $4}')
USED_H=$(df -h "$DATA_VOL" 2>/dev/null | awk 'NR==2{print $3}')
if ! [[ "$USED_PCT" =~ ^[0-9]+$ ]]; then
  echo "$(date): ERROR could not read $DATA_VOL usage" >> "$LOG"
  exit 1
fi

stamp() { echo "$(date): $1" >> "$LOG"; }

# 1-per-hour dedup: fire if no flag OR flag older than 60min
should_fire() { local f="$1"; [ ! -f "$f" ] || [ -n "$(find "$f" -mmin +60 2>/dev/null)" ]; }

bus_nudge() {  # $1=priority $2=subject $3=body
  # MUST use the venv python (has psycopg2); launchd's bare python3 does not.
  "$ORCH_DIR/.venv/bin/python3" - "$@" <<'PY' 2>>"$LOG"
import sys, psycopg2
pri, subj, body = sys.argv[1], sys.argv[2], sys.argv[3]
import os
url=None
with open(os.path.expanduser('~/wingmen/orchestrator/.env')) as f:
    for line in f:
        line=line.strip()
        for k in ('SUPABASE_DB_URL','DATABASE_URL','ORCH_DATABASE_URL'):
            if line.startswith(k+'='):
                url=line.split('=',1)[1].strip().strip('"').strip("'"); break
        if url: break
conn=psycopg2.connect(url); cur=conn.cursor()
cur.execute("SET app.current_agent_id='orch-console'")
cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
  VALUES ('orch-console','orch-console','blocker',%s,%s,%s)""",(pri,subj,body))
conn.commit(); cur.close(); conn.close()
PY
}

if [ "$USED_PCT" -ge "$CRIT_PCT" ]; then
  if should_fire "$CRIT_FLAG"; then
    bus_nudge "P1" "DISK CRITICAL: ${DATA_VOL} at ${USED_PCT}% (only ${AVAIL_H} free)" \
      "The Mac Mini DATA volume is ${USED_PCT}% full (${USED_H} used, ${AVAIL_H} free). At full it crashes watchdogs/DB writes with [Errno 28]. CLEAR NOW: npm cache clean --force; delete .next build caches (older than 60min) under ~/wingmen/projects; ~/.cache, brew cleanup -s. See the 2026-08-21 incident."
    "$ORCH_DIR/scripts/nazim_send.sh" \
      "⚠️ Mac Mini disk is nearly full — ${USED_PCT}% used, only ${AVAIL_H} free. I'm clearing regenerable space (caches + build files) now; flagging you because at 100% things start crashing. Will confirm once it's back in the safe range." "@console disk-critical" >/dev/null 2>&1
    touch "$CRIT_FLAG"; stamp "CRIT alert sent — ${USED_PCT}% (${AVAIL_H} free)"
  fi
elif [ "$USED_PCT" -ge "$WARN_PCT" ]; then
  if should_fire "$WARN_FLAG"; then
    bus_nudge "P2" "DISK WARN: ${DATA_VOL} at ${USED_PCT}% (${AVAIL_H} free)" \
      "Mac Mini DATA volume at ${USED_PCT}% (${AVAIL_H} free) — above the ${WARN_PCT}% warn line. Clear regenerable space before it gets critical: npm cache clean --force; .next caches under ~/wingmen/projects; ~/.cache; brew cleanup -s. Operator NOT alerted yet (console handles the warn tier)."
    touch "$WARN_FLAG"; stamp "WARN nudge sent — ${USED_PCT}% (${AVAIL_H} free)"
  fi
else
  # healthy — clear dedup so a future breach re-alerts
  [ -f "$WARN_FLAG" ] && { rm -f "$WARN_FLAG"; stamp "recovered below warn (${USED_PCT}%)"; }
  [ -f "$CRIT_FLAG" ] && rm -f "$CRIT_FLAG"
  stamp "healthy — ${USED_PCT}% (${AVAIL_H} free)"
fi
