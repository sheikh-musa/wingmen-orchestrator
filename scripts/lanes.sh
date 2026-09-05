#!/usr/bin/env bash
# lanes.sh — tmux fleet manager for Wingmen CC build lanes.
#
# Each lane is a tmux session that runs launch_dangerous_cc.sh inside a worktree.
# IDEMPOTENT: a lane is skipped if a `claude` process already has its dir as cwd,
# so re-running never double-launches a dangerous (auto-pushing) CC onto a tree
# that already has one — the only failure mode that can actually lose work.
#
# The lane roster is READ from the `fleet_lanes` table (desired_state='up',
# launcher='launch_dangerous_cc.sh') — never a hardcoded list. Adding a lane
# is a DB row, not an edit to this file (op#19103 item 5a). A lane with
# desired_state != 'up' is invisible to this script by design: it is not
# lanes.sh's roster until someone flips it.
#
# Usage:
#   lanes.sh ls          # show fleet status (running / down) for every UP-desired lane
#   lanes.sh up <lane>   # boot one named lane (must be desired_state='up' in fleet_lanes)
#   lanes.sh attach <lane>   # attach to a lane's tmux session
#
# NOTE: there is deliberately no bare `lanes.sh up` (audit finding: it used to
# boot every lane in a hardcoded list regardless of whether that lane was
# meant to be running, which is how stray lanes got booted). Boot lanes one
# at a time, by name.
set -euo pipefail

LAUNCHER="$HOME/wingmen/orchestrator/scripts/launch_dangerous_cc.sh"

_require_dsn() {
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL not set — lanes.sh reads its roster from fleet_lanes, no fallback." >&2
    exit 2
  fi
}

# lane <TAB> worktree_path, for every fleet_lanes row this script owns
# (launcher=launch_dangerous_cc.sh). desired_state is a separate column so ls
# can show BOTH the registry's intent and the observed tmux/process state.
lane_rows() {
  _require_dsn
  psql "$DATABASE_URL" -t -A -F $'\t' -c \
    "SELECT lane, desired_state, worktree_path FROM fleet_lanes
     WHERE launcher = 'launch_dangerous_cc.sh' AND worktree_path IS NOT NULL
     ORDER BY lane;"
}

up_lane_row() {
  local want="$1"
  _require_dsn
  psql "$DATABASE_URL" -t -A -F $'\t' -c \
    "SELECT worktree_path FROM fleet_lanes
     WHERE lane = '${want}' AND launcher = 'launch_dangerous_cc.sh'
       AND desired_state = 'up' AND worktree_path IS NOT NULL;"
}

lane_exists_any_state() {
  local want="$1"
  _require_dsn
  psql "$DATABASE_URL" -t -A -c \
    "SELECT desired_state FROM fleet_lanes WHERE lane = '${want}' AND launcher = 'launch_dangerous_cc.sh';"
}

# Absolute cwd of every running `claude --dangerously-skip-permissions` process.
running_cwds() {
  for pid in $(pgrep -f 'claude --dangerously-skip-permissions' 2>/dev/null); do
    lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'
  done
}

dir_has_claude() {
  local target="$1"
  running_cwds | grep -Fxq "$target"
}

cmd_ls() {
  printf '%-24s %-8s %-8s %s\n' LANE DESIRED STATUS DIR
  while IFS=$'\t' read -r name desired dir; do
    [ -z "$name" ] && continue
    if [ ! -d "$dir" ]; then status="MISSING"
    elif dir_has_claude "$dir"; then status="running"
    else status="down"; fi
    printf '%-24s %-8s %-8s %s\n' "$name" "$desired" "$status" "$dir"
  done < <(lane_rows)
}

boot_one() {
  local name="$1" dir="$2"
  if [ ! -d "$dir" ]; then echo "SKIP $name — dir missing: $dir"; return; fi
  if dir_has_claude "$dir"; then echo "SKIP $name — claude already running in $dir"; return; fi
  if tmux has-session -t "$name" 2>/dev/null; then
    echo "SKIP $name — tmux session already exists (attach with: lanes.sh attach $name)"; return
  fi
  tmux new-session -d -s "$name" -c "$dir" "$LAUNCHER"
  echo "BOOTED $name → tmux session '$name' ($dir)"
}

cmd_up() {
  local want="${1:?usage: lanes.sh up <lane> — bare 'up' was removed, see the header comment}"
  local dir
  dir="$(up_lane_row "$want")"
  if [ -z "$dir" ]; then
    local state
    state="$(lane_exists_any_state "$want")"
    if [ -n "$state" ]; then
      echo "REFUSE $want — fleet_lanes.desired_state='$state', not 'up'. Flip the row first." >&2
    else
      echo "REFUSE $want — no launch_dangerous_cc.sh row named '$want' in fleet_lanes." >&2
    fi
    exit 1
  fi
  boot_one "$want" "$dir"
}

# WIND A LANE DOWN — the counterpart `up` never had. Until 2026-08-16 this substrate was
# one-directional: lanes could be spun up and nothing anywhere ended one, which is how ten
# irsyad-family lanes came to sit parked holding context nobody was reading. The doctrine
# line calling the manual pen "interim until the autoscaler subsumes it" described code that
# did not exist.
#
# The GATES ARE NOT HERE ON PURPOSE. They live in scripts/lib/lane_winddown.py, unit-tested
# without a live tmux or DB, and the SRE's idle detector will call the SAME predicate — so a
# lane can never be ended by one path under rules the other path would have refused. Ending a
# session is harsher than a recycle (there is no boot afterwards; context that was only in
# that session is gone), so every gate fails CLOSED: busy, unread bus rows, a stale/missing
# handoff, real staged text, or an unreadable composer all refuse.
cmd_down() {
  local lane="${1:?usage: lanes.sh down <lane> [--kill]}"; shift || true
  "$HOME/wingmen/orchestrator/.venv/bin/python3" "$HOME/wingmen/orchestrator/scripts/lib/lane_winddown.py" "$lane" "$@"
}

case "${1:-ls}" in
  ls) cmd_ls ;;
  up) cmd_up "${2:-}" ;;
  down) shift; cmd_down "$@" ;;
  attach) tmux attach -t "${2:?usage: lanes.sh attach <lane>}" ;;
  *) echo "usage: lanes.sh {ls|up <lane>|down <lane> [--kill]|attach <lane>}" >&2; exit 2 ;;
esac
