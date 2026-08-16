#!/usr/bin/env bash
# sendkeys_catcher_mini.sh — WHO is typing into this host's panes?
#
# WHY THIS EXISTS (2026-08-16, operator-authorised: "hunt it down"). Instruction-shaped,
# operator-voiced text keeps appearing UNSENT in lane composers on the Mac Mini — including
# "deploy wingmen.dev" and, after I said in writing that I required a confirmation, the exact
# confirmation phrase "yes that was me, deploy wingmen.dev". The operator states on the logged
# channel that none of it is him and that he never types into terminals.
#
# This is a RECURRENCE, not a new phenomenon: scripts/sendkeys_catcher.sh was written on
# 2026-07-05 for the same thing. That copy is hardcoded to the STUDIO (socket /private/tmp/
# tmux-502/default, /Users/Musa paths), so it cannot see anything here. This is the Mini's.
#
# WHAT IT DOES: polls tight for (A) transient non-server holders of the Mini's tmux socket and
# (B) any `tmux send-keys` invocation in flight — logging PID, PPID, full command and both
# parents. A send-keys process is short-lived, so the poll is deliberately fast and cheap.
#
# READ-ONLY. It observes; it never types, never clears, never resets. Safe to run alongside
# everything else, and safe to kill at any time.
#
# Usage: scripts/sendkeys_catcher_mini.sh [seconds]   (default 1800; 0 = run until killed)
set -uo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ORCH_DIR/logs/sendkeys_catcher_mini.log"
DURATION="${1:-1800}"
# The Mini runs the /usr/local/bin/tmux server on socket tmux-501 (NOT homebrew's) —
# see reference_mini_tmux_two_binaries_socket. Catch both sockets rather than assume.
SOCKS="/private/tmp/tmux-501/default /tmp/tmux-501/default"

mkdir -p "$ORCH_DIR/logs"
printf '=== mini catcher start %s pid=%s duration=%ss ===\n' \
  "$(date -u +%FT%TZ)" "$$" "$DURATION" >> "$OUT"

log_pid() {
  local pid="$1" how="$2"
  [ -z "$pid" ] && return
  local cmd ppid pcmd gppid gpcmd
  cmd=$(ps -o command= -p "$pid" 2>/dev/null | head -c 400)
  [ -z "$cmd" ] && return                       # already exited; nothing truthful to say
  ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  pcmd=$(ps -o command= -p "${ppid:-0}" 2>/dev/null | head -c 240)
  gppid=$(ps -o ppid= -p "${ppid:-0}" 2>/dev/null | tr -d ' ')
  gpcmd=$(ps -o command= -p "${gppid:-0}" 2>/dev/null | head -c 240)
  printf '%s [%s] pid=%s cmd=[%s]\n            ppid=%s parent=[%s]\n            gppid=%s grandparent=[%s]\n' \
    "$(date -u +%FT%TZ)" "$how" "$pid" "$cmd" "${ppid:-?}" "$pcmd" "${gppid:-?}" "$gpcmd" >> "$OUT"
}

START=$(date +%s)
while :; do
  # (A) transient socket holders that are not the tmux server itself
  for s in $SOCKS; do
    [ -S "$s" ] || continue
    lsof "$s" 2>/dev/null | awk 'NR>1 && $1!="tmux" {print $2}' \
      | while read -r p; do log_pid "$p" sock; done
  done
  # (B) an actual `tmux send-keys` in flight. Match the tmux BINARY running send-keys, not a
  # shell whose text merely mentions it, and never this catcher or its own grep.
  ps -eo pid=,command= 2>/dev/null \
    | grep -E '(^|/| )tmux( -[^ ]+)* .*send-keys' \
    | grep -vE 'sendkeys_catcher|grep -E' \
    | awk '{print $1}' \
    | while read -r p; do [ "$p" != "$$" ] && log_pid "$p" sendkeys; done

  if [ "$DURATION" != "0" ]; then
    [ $(( $(date +%s) - START )) -ge "$DURATION" ] && break
  fi
  sleep 0.2
done
printf '=== mini catcher end %s ===\n' "$(date -u +%FT%TZ)" >> "$OUT"
