#!/usr/bin/env bash
# fire_window.sh — bash side of the recycle fire-window hold (see fire_window.py).
#
# A reset owns a pane for a few seconds: it wipes the composer, types /clear, submits it,
# then types the boot instruction. Anything that send-keys into that pane meanwhile jams
# the sequence and the body comes back half-initialised. `reset_nazim.sh` used to guard
# this by bootout-ing ONE daemon; at least eight things on this host can type into a pane,
# and the next one written would not have been on that list. So senders consult a lock.
#
# Source this, then call ONCE before the first send-keys:
#     . "$ORCH_DIR/scripts/lib/fire_window.sh"
#     fire_window_hold "$SESS" 180 "reset_nazim fire window"
# The release is installed as an EXIT trap by the same call — a crashed reset must never
# leave a body quiesced (the TTL bounds it; the trap prevents it).
#
# SCOPE LIMIT, stated rather than implied: the lock is per-HOST. It protects a pane from
# senders running on the same machine. A pane on another host is protected by THAT host's
# lock, taken by the reset that runs there.

_fire_window_py() {
  local orch="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
  if [ -x "$orch/.venv/bin/python3" ]; then echo "$orch/.venv/bin/python3"; else echo python3; fi
}

_FIRE_WINDOW_SESSION=""
fire_window_release_trap() {
  [ -n "$_FIRE_WINDOW_SESSION" ] || return 0
  "$(_fire_window_py)" "${ORCH_DIR:-$HOME/wingmen/orchestrator}/scripts/lib/fire_window.py" \
    release "$_FIRE_WINDOW_SESSION" 2>/dev/null || true
  _FIRE_WINDOW_SESSION=""
}

fire_window_hold() {
  local sess="${1:?fire_window_hold <session> [ttl] [reason]}"
  local ttl="${2:-180}"
  local reason="${3:-fire window}"
  _FIRE_WINDOW_SESSION="$sess"
  # Chain onto any EXIT trap the caller already installed rather than clobbering it — the
  # reset scripts use EXIT to restore their bus-notify daemon, and losing that restore
  # would dangle notify-off, the exact hand-cleanup state that trap exists to prevent.
  local prior; prior="$(trap -p EXIT | sed -E "s/^trap -- '(.*)' EXIT$/\1/")"
  if [ -n "$prior" ] && [ "$prior" != "trap -- EXIT" ]; then
    trap "$prior; fire_window_release_trap" EXIT
  else
    trap 'fire_window_release_trap' EXIT
  fi
  "$(_fire_window_py)" "${ORCH_DIR:-$HOME/wingmen/orchestrator}/scripts/lib/fire_window.py" \
    hold "$sess" --ttl "$ttl" --reason "$reason" >/dev/null 2>&1 \
    && echo "[fire_window] held '$sess' for ${ttl}s — other keystroke senders will stand off" \
    || echo "[fire_window] WARNING: could not take the hold on '$sess' — proceeding UNQUIESCED" >&2
}
