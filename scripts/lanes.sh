#!/usr/bin/env bash
# lanes.sh — tmux fleet manager for Wingmen CC build lanes.
#
# Each lane is a tmux session that runs launch_dangerous_cc.sh inside a worktree.
# IDEMPOTENT: a lane is skipped if a `claude` process already has its dir as cwd,
# so re-running never double-launches a dangerous (auto-pushing) CC onto a tree
# that already has one — the only failure mode that can actually lose work.
#
# ATOMIC CLAIM (CAI-RESP-422b): the pgrep check + boot are NOT one step, so two
# concurrent `up`s (e.g. a fleet boot racing a watchdog respawn) could BOTH pass
# dir_has_claude() and BOTH launch onto one tree — the TOCTOU work-loss race. So
# boot_one wraps check+claim+boot in a per-lane critical section held by an atomic
# `mkdir` lock (portable on macOS — no flock). Fail-closed: if the claim is held
# by a LIVE claimer we SKIP; a lock left by a hard-killed prior run is reclaimed
# via holder-PID liveness. Locks live under $LANE_LOCK_ROOT and are released on
# exit (normal, `set -e` abort, or SIGINT/SIGTERM) by the EXIT trap. The pgrep
# check is KEPT as a secondary guard inside the critical section.
#
# Usage:
#   lanes.sh ls         # show fleet status (running / down) — no side effects
#   lanes.sh up         # boot every DOWN lane in its own tmux session
#   lanes.sh up <lane>  # boot one named lane
#   lanes.sh attach <lane>   # attach to a lane's tmux session
set -euo pipefail

LAUNCHER="$HOME/wingmen/orchestrator/scripts/launch_dangerous_cc.sh"

# --- Fleet declaration: lane_name <TAB> working_directory --------------------
# Edit this block to add/remove lanes. Keep paths absolute.
read -r -d '' LANES <<EOF || true
mirror	$HOME/.config/superpowers/worktrees/ihsanos/mirror
scholar	$HOME/wingmen/projects/ai-scholar
tarbiyah	$HOME/wingmen/projects/tarbiyah
EOF
# -----------------------------------------------------------------------------

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

# --- Atomic per-lane claim (CAI-RESP-422b) -----------------------------------
# mkdir is atomic (create-or-fail) on every POSIX fs, so it is our lock. Held
# locks are tracked in _HELD_LOCKS and released by the EXIT trap, which fires on
# normal return, `set -e` abort, and SIGINT/SIGTERM — so a claim never outlives
# the process that took it (the SIGKILL edge is covered by stale reclaim below).
LANE_LOCK_ROOT="${TMPDIR:-/tmp}/wingmen-lane-locks"
_HELD_LOCKS=()
_release_lane_locks() {
  local d
  for d in ${_HELD_LOCKS[@]+"${_HELD_LOCKS[@]}"}; do rm -rf "$d" 2>/dev/null || true; done
}
trap _release_lane_locks EXIT INT TERM

# claim_lane <name>: 0 = claimed (caller owns the critical section), 1 = held by
# a LIVE claimer (fail-closed → caller SKIPs).
claim_lane() {
  # Separate declarations: on a single `local` line bash expands every RHS
  # before any assignment, so `${name}` in a combined decl would be unbound
  # under `set -u`. Assign name first, then reference it.
  local name="$1"
  local lockdir="$LANE_LOCK_ROOT/${name}.lockd"
  local holder
  mkdir -p "$LANE_LOCK_ROOT" 2>/dev/null || true
  if mkdir "$lockdir" 2>/dev/null; then
    printf '%s\n' "$$" > "$lockdir/pid"; _HELD_LOCKS+=("$lockdir"); return 0
  fi
  # Held — reclaim ONLY if the recorded holder is provably dead (stale SIGKILL).
  holder="$(cat "$lockdir/pid" 2>/dev/null || true)"
  if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
    rm -rf "$lockdir" 2>/dev/null || true
    if mkdir "$lockdir" 2>/dev/null; then
      printf '%s\n' "$$" > "$lockdir/pid"; _HELD_LOCKS+=("$lockdir"); return 0
    fi
  fi
  return 1
}

lane_dirs() { printf '%s\n' "$LANES" | sed '/^[[:space:]]*$/d'; }

cmd_ls() {
  printf '%-16s %-8s %s\n' LANE STATUS DIR
  while IFS=$'\t' read -r name dir; do
    [ -z "$name" ] && continue
    if [ ! -d "$dir" ]; then status="MISSING"
    elif dir_has_claude "$dir"; then status="running"
    else status="down"; fi
    printf '%-16s %-8s %s\n' "$name" "$status" "$dir"
  done < <(lane_dirs)
}

boot_one() {
  local name="$1" dir="$2"
  if [ ! -d "$dir" ]; then echo "SKIP $name — dir missing: $dir"; return; fi
  # Acquire the per-lane claim BEFORE the check, so check+claim+boot is one
  # atomic critical section — a concurrent `up` cannot slip between them.
  if ! claim_lane "$name"; then
    echo "SKIP $name — claim held by a concurrent 'up' (fail-closed)"; return
  fi
  # ── critical section (claim held; released by EXIT trap) ──
  # pgrep kept as the secondary guard; tmux has-session is the third.
  if dir_has_claude "$dir"; then
    echo "SKIP $name — claude already running in $dir"
  elif tmux has-session -t "$name" 2>/dev/null; then
    echo "SKIP $name — tmux session already exists (attach with: lanes.sh attach $name)"
  else
    tmux new-session -d -s "$name" -c "$dir" "$LAUNCHER"
    echo "BOOTED $name → tmux session '$name' ($dir)"
  fi
}

cmd_up() {
  local want="${1:-}"
  while IFS=$'\t' read -r name dir; do
    [ -z "$name" ] && continue
    if [ -n "$want" ] && [ "$want" != "$name" ]; then continue; fi
    boot_one "$name" "$dir"
  done < <(lane_dirs)
}

case "${1:-ls}" in
  ls) cmd_ls ;;
  up) cmd_up "${2:-}" ;;
  attach) tmux attach -t "${2:?usage: lanes.sh attach <lane>}" ;;
  *) echo "usage: lanes.sh {ls|up [lane]|attach <lane>}" >&2; exit 2 ;;
esac
