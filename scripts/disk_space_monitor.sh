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
# WHAT: checks the DATA volume every run, gated on FREE SPACE (GB), not percent —
#   the volume is 233Gi total, so percent-of-capacity floats noisily (89-97%)
#   across what is actually a healthy 8-20GB-free range; GB free is the number
#   that actually predicts a crash (same lesson as the file-history-prune
#   WARN_FREE/HARD_FREE retune, PR#463, bus #73314/#73375).
#   - WARN  (< WARN_FREE_GB, default 9): LOCAL LOG ONLY. No bus row to anyone —
#           cc-fleet-health already runs its own reclaim passes on a schedule;
#           a bus nudge at this band was crying wolf at a healthy-ish 8-9GB free.
#   - HARD  (< HARD_FREE_GB, default 6): bus P1 to orch-console + a direct ELI5
#           Telegram alert to the operator (nazim_send.sh) — genuine near-crash,
#           action is urgent.
#   - Healthy (>= WARN_FREE_GB): clears the dedup flag.
# Dedup: one HARD alert per hour (flag file in /tmp). If /tmp itself is on the
# full volume the flag write may fail — acceptable: worst case a duplicate HARD
# alert, which for a near-full disk is the right failure direction.
#
# ROUTING history (bus #68342/#68390, 2026-10-15; retuned again 2026-10-22 per
# bus #73375): WARN used to nudge orch-console, then cc-fleet-health, on every
# %-based crossing — both were noise once the real signal is free-GB, not
# percent. WARN is now silent-but-logged; only HARD (the real operator-facing
# floor) reaches anyone.
#
# Scoped to the console body: on HARD it writes the bus row to orch-console +
# the operator's nazim-console voice. It NEVER touches lanes or the hub pen.
set -uo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WARN_FREE_GB="${DISK_WARN_FREE_GB:-9}"   # local-log-only early signal
HARD_FREE_GB="${DISK_HARD_FREE_GB:-6}"   # genuine near-crash floor, pages
DATA_VOL="${DISK_DATA_VOL:-/System/Volumes/Data}"
CRIT_FLAG="/tmp/disk_monitor_crit_sent"
LOG="$ORCH_DIR/logs/disk_space_monitor.log"

# --- read the DATA volume usage (the one that actually fills) ---
USED_PCT=$(df -k "$DATA_VOL" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
AVAIL_KB=$(df -k "$DATA_VOL" 2>/dev/null | awk 'NR==2{print $4}')
AVAIL_H=$(df -h "$DATA_VOL" 2>/dev/null | awk 'NR==2{print $4}')
USED_H=$(df -h "$DATA_VOL" 2>/dev/null | awk 'NR==2{print $3}')
if ! [[ "$USED_PCT" =~ ^[0-9]+$ ]] || ! [[ "$AVAIL_KB" =~ ^[0-9]+$ ]]; then
  echo "$(date): ERROR could not read $DATA_VOL usage" >> "$LOG"
  exit 1
fi
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))

stamp() { echo "$(date): $1" >> "$LOG"; }

# 1-per-hour dedup: fire if no flag OR flag older than 60min
should_fire() { local f="$1"; [ ! -f "$f" ] || [ -n "$(find "$f" -mmin +60 2>/dev/null)" ]; }

bus_nudge() {  # $1=priority $2=subject $3=body $4=to_agent (default orch-console)
  # MUST use the venv python (has psycopg2); launchd's bare python3 does not.
  "$ORCH_DIR/.venv/bin/python3" - "$@" <<'PY' 2>>"$LOG"
import sys, psycopg2
pri, subj, body = sys.argv[1], sys.argv[2], sys.argv[3]
to_agent = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else 'orch-console'
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
  VALUES ('orch-console',%s,'blocker',%s,%s,%s)""",(to_agent,pri,subj,body))
conn.commit(); cur.close(); conn.close()
PY
}

if [ "$AVAIL_GB" -lt "$HARD_FREE_GB" ]; then
  if should_fire "$CRIT_FLAG"; then
    bus_nudge "P1" "DISK CRITICAL: ${DATA_VOL} at ${AVAIL_H} free (${USED_PCT}%)" \
      "The Mac Mini DATA volume has only ${AVAIL_H} free (${USED_PCT}% full, ${USED_H} used) — below the ${HARD_FREE_GB}GB hard floor. At full it crashes watchdogs/DB writes with [Errno 28]. CLEAR NOW: npm cache clean --force; delete .next build caches (older than 60min) under ~/wingmen/projects; ~/.cache, brew cleanup -s. See the 2026-08-21 incident."
    "$ORCH_DIR/scripts/nazim_send.sh" \
      "⚠️ Mac Mini disk is nearly full — only ${AVAIL_H} free (${USED_PCT}% used). I'm clearing regenerable space (caches + build files) now; flagging you because at 100% things start crashing. Will confirm once it's back in the safe range." "@console disk-critical" >/dev/null 2>&1
    touch "$CRIT_FLAG"; stamp "HARD alert sent (paged) — ${AVAIL_H} free / ${USED_PCT}%"
  fi
elif [ "$AVAIL_GB" -lt "$WARN_FREE_GB" ]; then
  # local-log-only: no bus row, no page. cc-fleet-health's own reclaim passes
  # + scheduled health checks already watch this band; a bus nudge here was
  # the noise this retune exists to remove (bus #73375).
  stamp "WARN (log-only, no page) — ${AVAIL_H} free / ${USED_PCT}%"
else
  # healthy — clear dedup so a future HARD breach re-alerts
  [ -f "$CRIT_FLAG" ] && { rm -f "$CRIT_FLAG"; stamp "recovered above hard floor (${AVAIL_H} free)"; }
  stamp "healthy — ${AVAIL_H} free (${USED_PCT}%)"
fi
