#!/usr/bin/env bash
# row_ceiling.sh — a per-ROW LIFETIME delivery ceiling, sourced by lane_nudge.sh
# (Nazim #43063/#43073; changes-requested #43114).
#
# WHY: lane_nudge is the ONE typing choke every waker funnels through — wake_agent's
# _verified_submit calls it, the wake_backstop_sweep pokes through wake_agent, AND the
# SLA watchdog calls lane_nudge directly. The agent_wake 5/5min cap is per-AGENT and only
# bounds the wake_agent path; the SLA watchdog bypasses it, #141 bounds only the backstop.
# So NOTHING bounded re-delivery of the SAME bus row — one stale row was typed into
# cc-substrate ~12x/13min. This lib caps deliveries PER ROW so ANY caller is bounded.
#
# DESIGN (post-review #43114):
#   * LIFETIME ceiling: ROW_CAP total deliveries per row_id, EVER — no rolling-window reset
#     (a 5-per-30-min window still allowed ~240/day of one stuck row). The count is the
#     number of delivery stamps in the row's file; nothing prunes it.
#   * FIXED, host-level, checkout-independent default dir ($HOME/.wingmen_state/rownudge) so
#     EVERY caller on the host (any checkout/worktree) shares ONE count for a row.
#   * FAIL-CLOSED: if the state dir can't be made/read, row_ceiling_ok returns 2 and the
#     caller REFUSES — a wake that doesn't land is recoverable; an unbounded loop is the bug.
#   * A separate GC (row_ceiling_gc) deletes row files not touched within ROW_GC_AGE_S so the
#     dir doesn't grow forever. That is cleanup of long-dead rows, NOT a reset of live budgets.
# When no row_id is passed (command nudges), lane_nudge never calls this — no ceiling.

ROW_CEILING_DIR="${ROW_CEILING_DIR:-$HOME/.wingmen_state/rownudge}"
ROW_CAP="${ROW_CAP:-5}"                    # LIFETIME max deliveries per row
ROW_GC_AGE_S="${ROW_GC_AGE_S:-604800}"     # 7d: GC a row file untouched this long
ROW_GC_INTERVAL_S="${ROW_GC_INTERVAL_S:-3600}"  # run GC at most once an hour (self-throttled)

# Keep a row_id filename-safe: anything outside [A-Za-z0-9_-] becomes '_', so a crafted or
# DB-shaped id (UUID, "../x") can never escape ROW_CEILING_DIR.
_row_ceiling_sanitize() { printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9_-' '_'; }
_row_ceiling_file() { printf '%s/%s' "$ROW_CEILING_DIR" "$(_row_ceiling_sanitize "$1")"; }

# Ensure the state dir exists and is writable. Return non-zero on failure (fail-closed).
_row_ceiling_ensure_dir() {
  mkdir -p "$ROW_CEILING_DIR" 2>/dev/null || return 1
  [ -w "$ROW_CEILING_DIR" ] || return 1
}

# Print the LIFETIME delivery count for a row (number of stamps). Returns non-zero on a
# state error so the caller can fail closed.
row_ceiling_count() {
  local f n
  _row_ceiling_ensure_dir || return 2
  f="$(_row_ceiling_file "$1")"
  [ -f "$f" ] || { echo 0; return 0; }
  n="$(grep -c . "$f" 2>/dev/null)" || return 2
  echo "${n:-0}"
}

# exit 0 = under the ceiling (OK to deliver); 1 = at/over the ceiling (cap hit);
# 2 = STATE ERROR (dir/count unavailable) -> caller must FAIL CLOSED (refuse).
row_ceiling_ok() {
  local n
  n="$(row_ceiling_count "$1")" || return 2
  case "$n" in ''|*[!0-9]*) return 2 ;; esac   # unparseable count = state error, fail closed
  [ "$n" -lt "$ROW_CAP" ]
}

# Record one verified delivery of this row (call ONLY after a verified submit). Returns
# non-zero on a write failure so the caller can log LOUD (the check already gated writability).
row_ceiling_record() {
  local f
  _row_ceiling_ensure_dir || return 2
  f="$(_row_ceiling_file "$1")"
  printf '%s\n' "$(date +%s)" >> "$f" 2>/dev/null || return 2
}

# GC: delete row files not modified within ROW_GC_AGE_S (cleanup so the dir doesn't grow
# forever). Does NOT reset a live row's budget — only removes long-dead rows. Run this
# periodically (e.g. from the wake_backstop_sweep loop), NOT per-delivery.
row_ceiling_gc() {
  [ -d "$ROW_CEILING_DIR" ] || return 0
  # never GC the throttle marker itself (its name starts with '.')
  find "$ROW_CEILING_DIR" -type f ! -name '.last_gc' -mmin +"$(( ROW_GC_AGE_S / 60 ))" -delete 2>/dev/null || true
}

# Self-throttled GC: safe to call on every lane_nudge invocation — it actually runs GC at
# most once per ROW_GC_INTERVAL_S (an mtime check on a marker the rest of the time), so GC
# is "separate" from the delivery budget without needing its own daemon. Never fatal.
row_ceiling_maybe_gc() {
  _row_ceiling_ensure_dir || return 0
  local marker="$ROW_CEILING_DIR/.last_gc"
  if [ -f "$marker" ] && find "$marker" -mmin -"$(( ROW_GC_INTERVAL_S / 60 ))" 2>/dev/null | grep -q .; then
    return 0   # GC'd within the interval — skip
  fi
  row_ceiling_gc
  : > "$marker" 2>/dev/null || true
}
