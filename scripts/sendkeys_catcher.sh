#!/usr/bin/env bash
# sendkeys_catcher.sh — D2 composer catch (no sudo, durable via launchd).
# The 2026-07-05 phantoms are a LOCAL Studio process running `tmux send-keys`
# into lane panes (MacBook exonerated by the key-revocation test). Two catches,
# polled tight: (A) transient non-server holders of the tmux socket; (B) any
# `tmux send-keys` command in flight (lingers longer than the socket connect).
# Whichever fires, it logs PID + parent + full command + timestamp.
SOCK="/private/tmp/tmux-502/default"
OUT="/Users/Musa/wingmen/orchestrator/logs/sendkeys_catcher.log"
echo "=== catcher start $(date -u +%FT%TZ) pid=$$ ===" >> "$OUT"
log_pid() {
  local pid="$1" how="$2"
  [ -z "$pid" ] && return
  local cmd ppid pcmd
  cmd=$(ps -o command= -p "$pid" 2>/dev/null | head -c 300)
  ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  pcmd=$(ps -o command= -p "$ppid" 2>/dev/null | head -c 160)
  printf '%s [%s] pid=%s ppid=%s cmd=[%s] parent=[%s]\n' \
    "$(date -u +%T.%3NZ)" "$how" "$pid" "$ppid" "$cmd" "$pcmd" >> "$OUT"
}
while true; do
  # (A) transient socket holders that aren't the tmux server
  lsof "$SOCK" 2>/dev/null | awk 'NR>1 && $1!="tmux" {print $2}' | while read -r p; do log_pid "$p" sock; done
  # (B) actual `tmux send-keys` invocations in flight — match only processes
  # whose command IS a tmux binary running send-keys (not shells/greps whose
  # text merely mentions "send-keys"). Exclude this catcher's own pid.
  ps -eo pid=,command= 2>/dev/null \
    | grep -E '(^|/| )tmux .*send-keys' \
    | grep -vE '(sendkeys_catcher|grep -E)' \
    | awk '{print $1}' | while read -r p; do [ "$p" != "$$" ] && log_pid "$p" sendkeys; done
  sleep 0.15
done
