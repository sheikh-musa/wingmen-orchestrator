#!/usr/bin/env bash
# row_ceiling.sh — a per-ROW delivery ceiling, sourced by lane_nudge.sh (Nazim #43063/#43073).
#
# WHY: lane_nudge is the ONE typing choke every waker funnels through — wake_agent's
# _verified_submit calls it, the wake_backstop_sweep pokes through wake_agent, AND the
# SLA watchdog calls lane_nudge directly. The existing 5/5min cap in agent_wake is
# per-AGENT and only bounds the wake_agent path; the SLA watchdog bypasses it entirely,
# and #141's quiesce bounds only the backstop. So NOTHING bounded re-delivery of the SAME
# bus row across all wakers — one stale row was typed into cc-substrate ~12x/13min
# (PID-correlated, WITHIN the per-agent cap). This lib caps deliveries PER ROW so ANY
# caller is bounded, keyed by the row_id the caller passes (env LANE_NUDGE_ROW_ID).
#
# COMPLEMENTS, does not duplicate, #141: #141 quiesces a stale row in the BACKSTOP; this
# bounds the same row across EVERY waker at the common choke. When no row_id is passed
# (command nudges: fleet_model, spawn_reviewer), there is no ceiling — backward-compatible.
#
# STATE: one file per row under ROW_CEILING_DIR, holding one epoch-second stamp per
# delivery; stamps older than ROW_WINDOW_S are pruned on every read/write. File-backed
# (survives lane_nudge process boundaries), self-cleaning, no DB.

ROW_CEILING_DIR="${ROW_CEILING_DIR:-${LANE_NUDGE_LOG_DIR:-}/.rownudge}"
ROW_CAP="${ROW_CAP:-5}"               # max deliveries of one row within the window
ROW_WINDOW_S="${ROW_WINDOW_S:-1800}"  # 30 min

# Keep a row_id filename-safe: anything outside [A-Za-z0-9_-] becomes '_', so a crafted
# or DB-shaped id (e.g. a UUID, or "../x") can never escape ROW_CEILING_DIR.
_row_ceiling_sanitize() { printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9_-' '_'; }

_row_ceiling_file() { printf '%s/%s' "$ROW_CEILING_DIR" "$(_row_ceiling_sanitize "$1")"; }

# Print the count of deliveries within the window, pruning stale stamps in place.
row_ceiling_count() {
  local f now cutoff kept
  f="$(_row_ceiling_file "$1")"
  now="$(date +%s)"; cutoff=$(( now - ROW_WINDOW_S ))
  [ -f "$f" ] || { echo 0; return 0; }
  # keep only stamps newer than the cutoff (numeric, robust to junk lines)
  kept="$(awk -v c="$cutoff" '/^[0-9]+$/ && $1 >= c' "$f" 2>/dev/null)"
  if [ -n "$kept" ]; then
    printf '%s\n' "$kept" > "$f" 2>/dev/null || true
    printf '%s\n' "$kept" | grep -c .
  else
    : > "$f" 2>/dev/null || true
    echo 0
  fi
}

# exit 0 => under the ceiling (OK to deliver); exit 1 => at/over the ceiling (REFUSE).
row_ceiling_ok() {
  local n
  n="$(row_ceiling_count "$1")"
  [ "$n" -lt "$ROW_CAP" ]
}

# Record one successful delivery of this row (call ONLY after a verified submit).
row_ceiling_record() {
  local f
  f="$(_row_ceiling_file "$1")"
  mkdir -p "$ROW_CEILING_DIR" 2>/dev/null || true
  row_ceiling_count "$1" >/dev/null   # prune first
  printf '%s\n' "$(date +%s)" >> "$f" 2>/dev/null || true
}
