#!/usr/bin/env bash
# switch_lane_token.sh — RE-TOKEN a Mini CC lane onto a DIFFERENT Claude Max
# account (token-pool conservation: spread lanes across Musa / Syed subs, op#7985/
# 7987 + project_migrate_lanes_to_musa_token / project_move_hub_irsyad_to_syed).
#
# A lane's Claude account = the CLAUDE_CODE_OAUTH_TOKEN it launched with; it is
# FIXED at process launch and CANNOT be changed in place. So re-tokening ==
# kill the tmux session + relaunch it via launch_lane_as.sh <token-file> in the
# SAME worktree. Identity is derived from the worktree (auto_agent_id resolves the
# family from pwd), so relaunching in the same dir keeps the SAME agent
# (e.g. cc-cosem-exams) — only the OAuth account (auth_fp) changes.
#
# This is the re-token sibling of reset_lane.sh (which does an in-place /clear and
# does NOT change the token — you cannot re-token without a fresh process).
#
# Usage:  scripts/switch_lane_token.sh [--force] <tmux-session> <token-file>
#   e.g.  scripts/switch_lane_token.sh cc-cosem-exams ~/.wingmen/keys/syed-oauth-token
#
# Safety (mirrors reset_lane.sh):
#   * Refuses a BUSY lane unless --force (or SWITCH_FORCE=1) — a re-token restarts
#     the lane FRESH (context is lost), so we guard against clobbering in-flight
#     work. --force warns loudly that in-flight work is discarded.
#   * Fail-CLOSED on the FORBIDDEN gazzabyte consumer token (cai ruling CAI-729):
#     ~/.wingmen/keys/gazzabyte-oauth-token is a CONSUMER Max token that is NEVER
#     valid for lane use. Refused by basename here (defense in depth — the console
#     endpoint's allowlist also excludes it).
#   * Never prints the token; only the sha256(token)[:12] fingerprint (auth_fp),
#     the same fact the launcher stamps to agent_status.
#
# Verifies the switch by polling agent_status.auth_fp for this session until it
# CHANGES to sha256(new-token)[:12] (the fp the relaunch stamps), then prints
# BEFORE -> AFTER + PASS/FAIL.
#
# Mini lanes run on the /usr/local/bin/tmux server (socket tmux-501/default), NOT
# /opt/homebrew/bin/tmux — see reference_mini_tmux_two_binaries_socket.
set -uo pipefail
cd "$HOME/wingmen/orchestrator" || { echo "ERROR: orch dir missing" >&2; exit 9; }
ORCH_DIR="$(pwd)"
set -a; source .env; set +a

_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$_LIB/composer_capture.sh" || { echo "ERROR: composer_capture.sh missing" >&2; exit 9; }

TM="${TM:-/usr/local/bin/tmux}"
[ -x "$TM" ] || TM="$(command -v tmux || echo /usr/local/bin/tmux)"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
LAUNCH_AS="$ORCH_DIR/scripts/launch_lane_as.sh"
FORBIDDEN_BASENAME="gazzabyte-oauth-token"
POLL_S="${SWITCH_POLL_S:-60}"

# ── arg parse: [--force] <session> <token-file> ──────────────────────────────
FORCE="${SWITCH_FORCE:-0}"
SESS=""
TOKFILE=""
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) if [ -z "$SESS" ]; then SESS="$a"; elif [ -z "$TOKFILE" ]; then TOKFILE="$a"; fi ;;
  esac
done
[ -n "$SESS" ]    || { echo "usage: switch_lane_token.sh [--force] <tmux-session> <token-file>" >&2; exit 2; }
[ -n "$TOKFILE" ] || { echo "usage: switch_lane_token.sh [--force] <tmux-session> <token-file>" >&2; exit 2; }

# ── 1. Token-file validation (fail-closed, loud) ─────────────────────────────
[ -r "$TOKFILE" ] || { echo "ERROR: token file not readable: $TOKFILE" >&2; exit 3; }
# GOVERNANCE gate (CAI-729): the gazzabyte consumer token is FORBIDDEN. Check both
# the given path AND its realpath (a symlink cannot smuggle it past the guard).
_bn="$(basename "$TOKFILE")"
_rp="$(cd "$(dirname "$TOKFILE")" 2>/dev/null && pwd -P)/$_bn"
_rpbn="$(basename "$_rp")"
if [ "$_bn" = "$FORBIDDEN_BASENAME" ] || [ "$_rpbn" = "$FORBIDDEN_BASENAME" ]; then
  echo "ERROR: '$FORBIDDEN_BASENAME' is a CONSUMER Max token FORBIDDEN for lane use (CAI-729). Refusing." >&2
  exit 4
fi

# Compute the NEW account fingerprint EXACTLY as launch_dangerous_cc.sh does:
#   printf '%s' "$(cat <file>)" | shasum -a 256 | cut -c1-12
# $(cat) strips the trailing newline so a file with/without one yields the same
# fp the launcher stamps (launch_lane_as.sh reads the token via $(cat file)).
NEW_FP="$(printf '%s' "$(cat "$TOKFILE")" | shasum -a 256 2>/dev/null | cut -c1-12)"
if [ -z "$NEW_FP" ] || [ "$NEW_FP" = "e3b0c44298fc" ]; then
  # e3b0c44298fc == sha256("") — an empty token file. Refuse: an empty token
  # would silently fall the lane back to API billing (same trap the launcher guards).
  echo "ERROR: token file is empty or unreadable (fp resolves to the empty-string hash). Refusing." >&2
  exit 4
fi

# ── 2. Resolve the live session + its worktree on the Mini socket ────────────
if ! "$TM" has-session -t "$SESS" 2>/dev/null; then
  echo "ERROR: tmux session '$SESS' not found on this host ($TM)." >&2; exit 1
fi
# The worktree is the pane's cwd: launch_dangerous_cc.sh cd's into CALLER_DIR
# (the -c dir) before exec'ing claude, and claude is the pane's foreground process,
# so pane_current_path == the lane's worktree. Relaunching with -c <worktree>
# keeps the lane's IDENTITY (auto_agent_id resolves the family from pwd).
WORKTREE="$("$TM" display-message -p -t "$SESS" '#{pane_current_path}' 2>/dev/null)"
if [ -z "$WORKTREE" ] || [ ! -d "$WORKTREE" ]; then
  echo "ERROR: could not resolve a valid worktree (session_path) for '$SESS' (got: '${WORKTREE:-}')." >&2
  exit 1
fi

# ── 3. Capture BEFORE fingerprint from agent_status ──────────────────────────
# Freshest auth_fp for this session (a relaunch leaves the old offline row behind
# with the same tmux_session, so order by updated_at DESC to read the live one).
_fp_for_session() {
  "$VENV_PY" - "$1" <<'PY' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(os.path.join(os.getcwd(), '.env'))
try:
    import psycopg
except ImportError:
    sys.exit(0)
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)
sess = sys.argv[1]
try:
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT auth_fp FROM agent_status WHERE tmux_session = %s "
                "ORDER BY updated_at DESC NULLS LAST LIMIT 1",
                (sess,),
            )
            row = cur.fetchone()
            if row and row[0]:
                print(row[0])
except Exception:
    pass
PY
}
BEFORE_FP="$(_fp_for_session "$SESS")"
BEFORE_FP="${BEFORE_FP//[$'\t\r\n ']/}"

echo "[switch_lane_token] session=$SESS  worktree=$WORKTREE"
echo "[switch_lane_token] BEFORE auth_fp=${BEFORE_FP:-<none>}  ->  target auth_fp=$NEW_FP"

# Idempotent short-circuit: already on the target account. Do NOT restart (that
# would needlessly wipe the lane's context for a no-op).
if [ -n "$BEFORE_FP" ] && [ "$BEFORE_FP" = "$NEW_FP" ]; then
  echo "[switch_lane_token] lane is ALREADY on target account ($NEW_FP) — no restart. PASS (no-op)."
  exit 0
fi

# ── 4. Busy guard (reuse reset_lane's composer/busy detection) ───────────────
pane_busy "$TM" "${SESS}:0.0"
if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
  echo "WARNING: '$SESS' shows a background-agent marker but the pane is FROZEN (byte-identical)." >&2
  echo "         Treating as NOT busy: a live wait animates. In-flight work, if any, is already lost." >&2
fi
if [ "$CC_BUSY" = 1 ]; then
  if [ "$FORCE" = "1" ]; then
    echo "WARNING: '$SESS' is BUSY — $CC_BUSY_REASON — --force set, re-tokening ANYWAY (in-flight work DISCARDED)." >&2
  else
    echo "ERROR: '$SESS' is BUSY — $CC_BUSY_REASON — refusing to re-token. Pass --force to override." >&2
    exit 5
  fi
fi

echo "⚠ RE-TOKEN RESTARTS THE LANE FRESH: the conversation/context is RESET. Identity ($SESS in"
echo "  $WORKTREE) is preserved; only the Claude account changes. Staged composer text is NOT preserved."

# ── 5. kill + relaunch on the new account ────────────────────────────────────
echo "[switch_lane_token] killing '$SESS' ..."
"$TM" kill-session -t "$SESS" 2>/dev/null || true
# Brief settle so the socket releases the session name before we recreate it.
sleep 1
# Build the launch command; %q-quote so a path with spaces cannot break the shell
# tmux runs the command under. Absolute paths (no PATH assumption under SSH/launchd).
printf -v CMD '%q %q' "$LAUNCH_AS" "$TOKFILE"
echo "[switch_lane_token] relaunching '$SESS' in $WORKTREE on the new account ..."
if ! "$TM" new-session -d -s "$SESS" -c "$WORKTREE" "$CMD"; then
  echo "ERROR: tmux new-session failed for '$SESS'. Lane is DOWN — relaunch manually:" >&2
  echo "       $TM new-session -d -s $SESS -c $WORKTREE \"$CMD\"" >&2
  exit 7
fi

# ── 6. Poll agent_status.auth_fp until it flips to the new fp (up to POLL_S) ──
echo "[switch_lane_token] verifying auth_fp flip (polling up to ${POLL_S}s) ..."
DEADLINE=$(( $(date -u +%s) + POLL_S ))
AFTER_FP=""
while [ "$(date -u +%s)" -lt "$DEADLINE" ]; do
  sleep 3
  AFTER_FP="$(_fp_for_session "$SESS")"
  AFTER_FP="${AFTER_FP//[$'\t\r\n ']/}"
  [ "$AFTER_FP" = "$NEW_FP" ] && break
done

echo "───────────────────────────────────────────────────────────"
echo "  session:   $SESS"
echo "  BEFORE fp: ${BEFORE_FP:-<none>}"
echo "  AFTER  fp: ${AFTER_FP:-<not-yet-registered>}"
echo "  target fp: $NEW_FP"
if [ "$AFTER_FP" = "$NEW_FP" ]; then
  echo "  RESULT:    PASS — lane re-tokened onto the new account."
  echo "───────────────────────────────────────────────────────────"
  exit 0
else
  echo "  RESULT:    FAIL — auth_fp has not flipped within ${POLL_S}s."
  echo "             The relaunch may still be building context (auth_fp is stamped"
  echo "             late in launch_dangerous_cc.sh). Check: $TM attach -t $SESS"
  echo "───────────────────────────────────────────────────────────"
  exit 8
fi
