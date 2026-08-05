#!/usr/bin/env bash
# launch_lane_as.sh — boot a CC lane on a SPECIFIC Claude Max account by reading its
# OAuth token from a 0600 key file and exporting CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE
# (which launch_dangerous_cc.sh applies AFTER sourcing .env, so it wins) then exec'ing
# the normal launcher. Used to spread lanes across multiple Max subs (e.g. Musa vs Syed)
# to pool weekly limits.
#
# WHY a wrapper: `tmux new-session` does NOT inherit an ad-hoc `VAR=... tmux ...` env
# prefix into the launched command, so the override must be set INSIDE the session. The
# token is read from the file here — it never appears in argv/ps (only the file PATH does).
#
# Usage (as the tmux command):  scripts/launch_lane_as.sh <token-file>
set -euo pipefail
TOKFILE="${1:?usage: launch_lane_as.sh <token-file>}"
[ -r "$TOKFILE" ] || { echo "launch_lane_as: token file not readable: $TOKFILE" >&2; exit 1; }
export CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE="$(cat "$TOKFILE")"
exec "$HOME/wingmen/orchestrator/scripts/launch_dangerous_cc.sh"
