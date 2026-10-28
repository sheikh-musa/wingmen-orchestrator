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
#   - WARN      (< WARN_FREE_GB, default 9): LOCAL LOG ONLY. No bus row to anyone —
#               cc-fleet-health already runs its own reclaim passes on a schedule;
#               a bus nudge at this band was crying wolf at a healthy-ish 8-9GB free.
#   - MANAGED   (< HARD_FREE_GB, default 6, >= EMERGENCY_FREE_GB): a REAL hard-floor
#               breach, but already diagnosed structural (capacity+I/O load, external
#               drive fix escalated to Musa — bus #73426/#73430) with NO new action.
#               LOG-FILE ONLY, no bus row at all (bus #73460 — same carve PR#468 gave
#               disk_autoremediate.py / file_history_prune.py; a lower-priority bus
#               row still rings the console's reconcile-doorbell, so this band must
#               stay fully off the bus, not just lower-priority on it).
#   - EMERGENCY (< EMERGENCY_FREE_GB, default 4): bus P1 to orch-console + a direct
#               ELI5 Telegram alert to the operator (nazim_send.sh) — genuine
#               near-crash, action is urgent.
#   - Healthy (>= WARN_FREE_GB): clears the dedup flag.
# Dedup: one EMERGENCY alert per hour (flag file in /tmp). If /tmp itself is on the
# full volume the flag write may fail — acceptable: worst case a duplicate alert,
# which for a near-full disk is the right failure direction.
#
# ROUTING history (bus #68342/#68390, 2026-10-15; retuned 2026-10-22 per bus #73375,
# then again per #73460): WARN used to nudge orch-console, then cc-fleet-health, on
# every %-based crossing — noise once the real signal is free-GB, not percent. Then
# the old single HARD tier paged P1 for an already-diagnosed-structural condition as
# steady-state kept falling (the same perpetual-noise pattern PR#466/#467/#468 killed
# on the other two disk pagers) — split into MANAGED (log-only) + EMERGENCY (loud).
#
# Scoped to the console body: on EMERGENCY it writes the bus row to orch-console +
# the operator's nazim-console voice. It NEVER touches lanes or the hub pen.
set -uo pipefail

ORCH_DIR="${ORCH_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WARN_FREE_GB="${DISK_WARN_FREE_GB:-9}"             # local-log-only early signal
HARD_FREE_GB="${DISK_HARD_FREE_GB:-6}"             # real floor breach, logged (managed band)
EMERGENCY_FREE_GB="${DISK_EMERGENCY_FREE_GB:-4}"   # genuine near-crash, always pages LOUD
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
  ORCH_DIR="$ORCH_DIR" "$ORCH_DIR/.venv/bin/python3" - "$@" <<'PY' 2>>"$LOG"
import sys, os
pri, subj, body = sys.argv[1], sys.argv[2], sys.argv[3]
to_agent = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else 'orch-console'
# Musa op#34845, bus #78757/#78846 (PR-B step 2): was a raw psycopg INSERT INTO
# agent_messages; routed through scripts/bus_send.py's send(). Self-relative via
# ORCH_DIR (bus #78935/#78954/#78963), same as the reset_*.sh family -- ORCH_DIR
# is passed in explicitly above since this heredoc has no preceding `cd` to it
# (launchd's cwd is not guaranteed) and a bare env var isn't inherited unless
# exported, which ORCH_DIR itself never is further up this script.
orch_dir = os.environ['ORCH_DIR']
url=None
with open(os.path.join(orch_dir, '.env')) as f:
    for line in f:
        line=line.strip()
        for k in ('SUPABASE_DB_URL','DATABASE_URL','ORCH_DATABASE_URL'):
            if line.startswith(k+'='):
                url=line.split('=',1)[1].strip().strip('"').strip("'"); break
        if url: break
sys.path.insert(0, orch_dir)
from scripts.bus_send import send as bus_send
bus_send('orch-console', to_agent, 'blocker', subj, body, pri, req=False, dsn=url)
PY
}

if [ "$AVAIL_GB" -ge "$EMERGENCY_FREE_GB" ] && [ -f "$CRIT_FLAG" ]; then
  # recovered above the real emergency line — clear dedup so a FUTURE dip below
  # EMERGENCY_FREE_GB re-alerts, even if we're still sitting in the managed band.
  rm -f "$CRIT_FLAG"; stamp "recovered above emergency floor (${AVAIL_H} free)"
fi

if [ "$AVAIL_GB" -lt "$EMERGENCY_FREE_GB" ]; then
  if should_fire "$CRIT_FLAG"; then
    bus_nudge "P1" "DISK CRITICAL: ${DATA_VOL} at ${AVAIL_H} free (${USED_PCT}%)" \
      "The Mac Mini DATA volume has only ${AVAIL_H} free (${USED_PCT}% full, ${USED_H} used) — below the ${EMERGENCY_FREE_GB}GB emergency floor. At full it crashes watchdogs/DB writes with [Errno 28]. CLEAR NOW: npm cache clean --force; delete .next build caches (older than 60min) under ~/wingmen/projects; ~/.cache, brew cleanup -s. See the 2026-08-21 incident."
    "$ORCH_DIR/scripts/nazim_send.sh" \
      "⚠️ Mac Mini disk is nearly full — only ${AVAIL_H} free (${USED_PCT}% used). I'm clearing regenerable space (caches + build files) now; flagging you because at 100% things start crashing. Will confirm once it's back in the safe range." "@console disk-critical" >/dev/null 2>&1
    touch "$CRIT_FLAG"; stamp "EMERGENCY alert sent (paged) — ${AVAIL_H} free / ${USED_PCT}%"
  fi
elif [ "$AVAIL_GB" -lt "$HARD_FREE_GB" ]; then
  # MANAGED band (bus #73460): a real hard-floor breach, already diagnosed structural
  # with no new action — LOG-FILE ONLY, no bus row at all (not even lower-priority;
  # any bus row still rings the console's reconcile-doorbell). Same carve PR#468 gave
  # disk_autoremediate.py / file_history_prune.py.
  stamp "MANAGED (log-only, no bus row) — ${AVAIL_H} free / ${USED_PCT}% (floor ${HARD_FREE_GB}GB, emergency ${EMERGENCY_FREE_GB}GB)"
elif [ "$AVAIL_GB" -lt "$WARN_FREE_GB" ]; then
  # local-log-only: no bus row, no page. cc-fleet-health's own reclaim passes
  # + scheduled health checks already watch this band; a bus nudge here was
  # the noise this retune exists to remove (bus #73375).
  stamp "WARN (log-only, no page) — ${AVAIL_H} free / ${USED_PCT}%"
else
  stamp "healthy — ${AVAIL_H} free (${USED_PCT}%)"
fi
