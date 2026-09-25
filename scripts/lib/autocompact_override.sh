#!/usr/bin/env bash
# autocompact_override.sh — resolve this lane's autocompact-% pilot override, robustly.
#
# WHY (Nazim #43192): launch_dangerous_cc.sh resolved the pilot session with an UNTARGETED
# `tmux display-message -p '#S'`. In a DETACHED launch (`tmux new-session -d ... "launch..."`)
# there is no current tmux client, so it returned EMPTY → the per-session
# `.<session>_autocompact_pct` marker was SILENTLY skipped (substrate pilot run-2 never got its
# override). A pilot knob that silently fails to apply is the "monitor manufacturing false
# confidence" class.
#
# resolve_autocompact_override <orch_dir>: echoes "<pct> <tier-file>" for this lane's override
# (per-session marker beats the fleet file), or NOTHING. The tmux session is resolved by the
# SHARED resolver (scripts/lib/lane_session.sh — TMUX_PANE > LANE_SESSION > $TMUX-guarded
# untargeted; #151 chain, extracted in the #152 review so model/autocompact/subagent share it).
# If the session can't be resolved but a per-session marker EXISTS, warn LOUD to stderr rather
# than silently skip. The caller range-checks/exports; default-off when nothing is echoed.
. "$(dirname "${BASH_SOURCE[0]}")/lane_session.sh"
resolve_autocompact_override() {
  local orch="$1" sess=""
  sess="$(resolve_lane_session)"
  if [ -n "$sess" ] && [ -r "$orch/.${sess}_autocompact_pct" ]; then
    printf '%s .%s_autocompact_pct' "$(tr -dc '0-9' < "$orch/.${sess}_autocompact_pct")" "$sess"
    return 0
  elif [ -r "$orch/.fleet_autocompact_pct" ]; then
    printf '%s .fleet_autocompact_pct' "$(tr -dc '0-9' < "$orch/.fleet_autocompact_pct")"
    return 0
  fi
  # Session unresolvable + a PER-SESSION marker present → LOUD (never silently skip a pilot).
  if [ -z "$sess" ]; then
    local m
    for m in "$orch"/.*_autocompact_pct; do
      [ -e "$m" ] || continue
      case "$m" in
        */.fleet_autocompact_pct) : ;;   # fleet fallback already handled above
        *) echo "⚠ autocompact: could NOT resolve tmux session (TMUX_PANE='${TMUX_PANE:-}', LANE_SESSION unset) — a per-session pilot marker ($m) EXISTS but is being SKIPPED. Set LANE_SESSION or launch inside a pane. (Nazim #43192)" >&2
           break ;;
      esac
    done
  fi
  return 0
}
