#!/usr/bin/env bash
# lanes.sh — tmux fleet manager for Wingmen CC build lanes.
#
# Each lane is a tmux session that runs launch_dangerous_cc.sh inside a worktree.
# IDEMPOTENT: a lane is skipped if a `claude` process already has its dir as cwd,
# so re-running never double-launches a dangerous (auto-pushing) CC onto a tree
# that already has one — the only failure mode that can actually lose work.
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
  if dir_has_claude "$dir"; then echo "SKIP $name — claude already running in $dir"; return; fi
  if tmux has-session -t "$name" 2>/dev/null; then
    echo "SKIP $name — tmux session already exists (attach with: lanes.sh attach $name)"; return
  fi
  tmux new-session -d -s "$name" -c "$dir" "$LAUNCHER"
  echo "BOOTED $name → tmux session '$name' ($dir)"
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
