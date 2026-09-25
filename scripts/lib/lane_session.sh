#!/usr/bin/env bash
# lane_session.sh — the fleet's ONE way to resolve "which tmux session is THIS lane?"
# from inside a launcher, robust to a DETACHED launch (Nazim #43192 + #152 review).
#
# WHY: launch_dangerous_cc.sh resolved the session with an UNTARGETED
# `tmux display-message -p '#S'`. In a DETACHED launch (`tmux new-session -d ... "launch"`)
# there is no current tmux client, so it returned EMPTY -> per-session markers
# (.<session>_model, .<session>_autocompact_pct, .<session>_subagent_model) were silently
# skipped; and OUTSIDE any client it returns the SERVER's current session (another lane),
# which would make this lane read a DIFFERENT lane's marker. #151 fixed this inside
# autocompact_override.sh; the #152 review pulled the identical chain out HERE so the model
# cascade + autocompact + subagent cascade all share ONE resolver (no drift, one fix).
#
# resolve_lane_session: echoes the resolved tmux session name, or NOTHING if unresolvable.
# Resolution order:
#   1. the pane's OWN target ($TMUX_PANE — set inside any tmux pane, incl. a detached one)
#   2. explicit $LANE_SESSION env (a launcher may pass it)
#   3. untargeted display-message — ONLY inside a tmux client ($TMUX set); outside a client it
#      returns the server's current session (another lane), so it is skipped.
# An UNRESOLVED (empty) result is meaningful: callers that gate on identity (e.g. the CAI-1170
# auditor clamp) MUST fail CLOSED on empty, since they cannot prove which lane this is.
resolve_lane_session() {
  local sess=""
  # Only the pane-TARGETED form when TMUX_PANE is actually set — `-t ""` would resolve to some
  # arbitrary live session (leaking the server's current session) and shadow LANE_SESSION.
  [ -n "${TMUX_PANE:-}" ] && sess="$(tmux display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null || true)"
  [ -n "$sess" ] || sess="${LANE_SESSION:-}"
  # Untargeted ONLY inside a tmux client ($TMUX set): outside any client it returns the server's
  # CURRENT session (e.g. another lane), which would resolve the WRONG lane. Skip it otherwise.
  if [ -z "$sess" ] && [ -n "${TMUX:-}" ]; then
    sess="$(tmux display-message -p '#S' 2>/dev/null || true)"
  fi
  printf '%s' "$sess"
}
