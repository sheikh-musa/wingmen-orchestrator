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
# CONVERSATION-PRESERVING (op#9742+): the relaunch RESUMES the lane's existing
# conversation on the new account instead of starting fresh. Before the kill we
# resolve the lane's ACTIVE claude session-id (the most-recently-modified
# ~/.claude/projects/<escaped-worktree>/<uuid>.jsonl — the conversation is LOCAL
# JSONL state, independent of the OAuth token) and relaunch with `-- --resume
# <id>`, so the prior turns replay while new API calls bill to the new account.
# If no session file is found we FALL BACK to the old FRESH relaunch (and say so).
#
# This is the re-token sibling of reset_lane.sh (which does an in-place /clear and
# does NOT change the token — you cannot re-token without a fresh process).
#
# Usage:  scripts/switch_lane_token.sh [--force] <tmux-session> <token-file>
#   e.g.  scripts/switch_lane_token.sh cc-cosem-exams ~/.wingmen/keys/syed-oauth-token
#
# Safety (mirrors reset_lane.sh):
#   * Refuses a BUSY lane unless --force (or SWITCH_FORCE=1) — a re-token restarts
#     the lane's PROCESS (in-flight work in the live turn is interrupted; the
#     resumed conversation replays only what was already persisted to the JSONL),
#     so we guard against clobbering in-flight work. --force warns loudly.
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
# FINGERPRINT-based forbidden set (op#10851 f/u): the basename guard alone missed an
# ALIAS of the forbidden gazzabyte token ('gzb-oauth-token', same fp). Refuse ANY
# token whose CONTENT fingerprints to a forbidden fp, whatever its filename — the
# same alias-proof CAI-729 guard the console now applies (space-separated fps).
FORBIDDEN_FPS="13589de86f29"
# Verify window: fp flip is quick, but a FULL resume replay of a large session can
# take >60s to render a healthy pane, so give the healthy-verify headroom (op#12030 f/u).
POLL_S="${SWITCH_POLL_S:-120}"

# ── arg parse: [--force] [--dry-run] <session> <token-file> ──────────────────
# --dry-run (op#10706 R3): resolve + PRINT the plan (account before->after, the
# conversation-resume id, busy state), then exit 0 changing NOTHING — the preview
# the console's gated apply shows before any armed relaunch. Mirrors
# switch_singleton_token.sh's --dry-run.
FORCE="${SWITCH_FORCE:-0}"
DRY="${SWITCH_DRY_RUN:-0}"
# --model-apply (op#10706 R3, cai-approved option i): a MODEL change takes effect by
# relaunching the SAME-account lane so it re-reads .<session>_model — so bypass the
# same-account no-op short-circuit for this intent (keep token, resume conversation).
MODEL_APPLY="${SWITCH_MODEL_APPLY:-0}"
# --summary / --full (op#12030 f/u): how to answer CC's large-session resume menu
# after a --resume relaunch. DEFAULT = full (non-lossy: preserve the lane's context;
# Musa is fresh, headroom is not the constraint for this migration — console call).
# --summary opts into the lighter summary-resume for future/token-pressured use.
RESUME_MODE="${SWITCH_RESUME_MODE:-full}"
SESS=""
TOKFILE=""
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --dry-run) DRY=1 ;;
    --model-apply) MODEL_APPLY=1 ;;
    --summary) RESUME_MODE=summary ;;
    --full) RESUME_MODE=full ;;
    *) if [ -z "$SESS" ]; then SESS="$a"; elif [ -z "$TOKFILE" ]; then TOKFILE="$a"; fi ;;
  esac
done
[ -n "$SESS" ]    || { echo "usage: switch_lane_token.sh [--force] [--dry-run] [--summary] <tmux-session> <token-file>" >&2; exit 2; }
[ -n "$TOKFILE" ] || { echo "usage: switch_lane_token.sh [--force] [--dry-run] [--summary] <tmux-session> <token-file>" >&2; exit 2; }

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
# Alias-proof forbidden-fp guard (op#10851 f/u): refuse the token by its CONTENT
# fingerprint, whatever its filename (the basename guard alone missed 'gzb-oauth-token').
case " $FORBIDDEN_FPS " in
  *" $NEW_FP "*)
    echo "ERROR: token fingerprint $NEW_FP is a FORBIDDEN consumer token (CAI-729), whatever its filename. Refusing." >&2
    exit 4 ;;
esac

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

# ── 2.5 Resolve the lane's ACTIVE claude session-id (for conversation resume) ─
# Claude Code stores per-project sessions as JSONL under
#   ~/.claude/projects/<escaped-worktree>/<session-id>.jsonl
# where the dir name is the ABSOLUTE worktree path with every '/' AND '.' replaced
# by '-' (verified: '.../ihsanos-irsyad.wt-coord' -> '...-ihsanos-irsyad-wt-coord').
# The lane's ACTIVE session is the most-recently-modified *.jsonl in that dir; its
# filename stem IS the session-id (a UUID). The conversation is LOCAL state, so
# resuming under a DIFFERENT OAuth token replays the prior turns while billing new
# calls to the new account. resolve_resume_session (composer_capture.sh) searches
# BOTH the dot->dash and legacy dot-preserved escapings — an earlier version
# replaced only '/', so a DOTTED worktree found NO session and relaunched FRESH,
# silently LOSING the lane's context (the irsyad-coord incident this fixes).
# Resolve BEFORE the kill. No session found → RESUME_ID empty → FRESH (logged loud).
RESUME_ID="$(resolve_resume_session "$WORKTREE")"
if [ -n "$RESUME_ID" ]; then
  echo "[switch_lane_token] active session resolved: $RESUME_ID (will RESUME on the new account)"
else
  echo "[switch_lane_token] ⚠ no active claude session found for worktree $WORKTREE — will relaunch FRESH (context NOT preserved)"
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
# Same-account short-circuit — SKIP it for an intentional model-apply relaunch.
if [ "$MODEL_APPLY" != "1" ] && [ -n "$BEFORE_FP" ] && [ "$BEFORE_FP" = "$NEW_FP" ]; then
  echo "[switch_lane_token] lane is ALREADY on target account ($NEW_FP) — no restart. PASS (no-op)."
  exit 0
fi

# The model that a relaunch would pick up (op#10706): the per-body .<session>_model
# pointer, else the fleet default. Shown in the model-apply preview.
_APPLY_MODEL=""
if [ -r "$ORCH_DIR/.${SESS}_model" ]; then
  _APPLY_MODEL="$(tr -d '[:space:]' < "$ORCH_DIR/.${SESS}_model")"
fi

# ── DRY-RUN (op#10706 R3): print the plan, change NOTHING, exit 0. Proves the
# resume path so a real apply never comes back blank. ────────────────────────
if [ "$DRY" = "1" ]; then
  pane_busy "$TM" "${SESS}:0.0" 2>/dev/null || true
  echo "[switch_lane_token] DRY-RUN — no kill, no relaunch, nothing changed."
  if [ "$MODEL_APPLY" = "1" ]; then
    echo "  would MODEL-APPLY: $SESS   ($WORKTREE)"
    echo "  account:        SAME ($NEW_FP) — relaunch to pick up model=${_APPLY_MODEL:-<fleet default>}"
  else
    echo "  would re-token: $SESS   ($WORKTREE)"
    echo "  account:        ${BEFORE_FP:-<none>}  ->  $NEW_FP"
  fi
  if [ -n "$RESUME_ID" ]; then
    echo "  would RESUME conversation: $RESUME_ID  (prior turns replay; identity + context preserved)"
  else
    echo "  would relaunch FRESH: no prior session found to resume for this worktree"
  fi
  echo "  lane currently busy: ${CC_BUSY:-0}  (a real apply refuses a BUSY lane unless --force)"
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
# Extended pre-switch guard (op#12046 flip-all footguns the foreground busy-check
# MISSES): (a) a RUNNING BACKGROUND SHELL — a re-token kills the tmux session and
# with it the live shell (real risk on a client-data lane like irsyad-import's
# tabung write); (b) a NON-EMPTY COMPOSER DRAFT — --resume does NOT preserve
# unsubmitted text, so a bounce silently LOSES it. Refuse (hold, exit 5) unless
# --force, so a group/flip-all sweep SKIPS these and reports them for retry rather
# than clobbering. (composer_parse's CC_GHOST guards against a dim history-ghost
# being mistaken for a real draft.)
_guard_txt="$("$TM" capture-pane -t "${SESS}:0.0" -p 2>/dev/null)"
if pane_has_bg_shell "$_guard_txt"; then
  if [ "$FORCE" = "1" ]; then
    echo "WARNING: '$SESS' has a RUNNING BACKGROUND SHELL — --force set, re-tokening ANYWAY (the shell WILL be killed)." >&2
  else
    echo "ERROR: '$SESS' has a RUNNING BACKGROUND SHELL — a re-token would kill it — refusing (hold). Let it finish or pass --force." >&2
    exit 5
  fi
fi
composer_parse_pane "$TM" "${SESS}:0.0"
if [ "${CC_EMPTY:-1}" = 0 ] && [ "${CC_GHOST:-0}" != 1 ]; then
  if [ "$FORCE" = "1" ]; then
    echo "WARNING: '$SESS' has an UNSUBMITTED COMPOSER DRAFT ('${CC_FLAT}') — --force set, re-tokening ANYWAY (the draft is LOST)." >&2
  else
    echo "ERROR: '$SESS' has an UNSUBMITTED COMPOSER DRAFT ('${CC_FLAT}') — --resume won't preserve it — refusing (hold). Submit or capture it first, or pass --force." >&2
    exit 5
  fi
fi

if [ -n "$RESUME_ID" ]; then
  echo "⚠ RE-TOKEN restarts the lane PROCESS but RESUMES the conversation ($RESUME_ID) on the new"
  echo "  account. Identity ($SESS in $WORKTREE) and the prior turns are preserved; only the Claude"
  echo "  account changes. The live in-flight turn (if any) is interrupted; staged composer text is NOT preserved."
else
  echo "⚠ RE-TOKEN RESTARTS THE LANE FRESH (no prior session found to resume): the conversation/context"
  echo "  is RESET. Identity ($SESS in $WORKTREE) is preserved; only the Claude account changes."
fi

# ── 5. kill + relaunch on the new account ────────────────────────────────────
echo "[switch_lane_token] killing '$SESS' ..."
"$TM" kill-session -t "$SESS" 2>/dev/null || true
# Brief settle so the socket releases the session name before we recreate it.
sleep 1
# Build the launch command; %q-quote so a path (or session-id) with spaces cannot
# break the shell tmux runs the command under. Absolute paths (no PATH assumption
# under SSH/launchd). With a RESUME_ID we forward `-- --resume <id>` through
# launch_lane_as.sh → launch_dangerous_cc.sh → claude, so the conversation resumes.
if [ -n "$RESUME_ID" ]; then
  printf -v CMD '%q %q -- --resume %q' "$LAUNCH_AS" "$TOKFILE" "$RESUME_ID"
  echo "[switch_lane_token] relaunching '$SESS' in $WORKTREE on the new account, RESUMING $RESUME_ID ..."
else
  printf -v CMD '%q %q' "$LAUNCH_AS" "$TOKFILE"
  echo "[switch_lane_token] relaunching '$SESS' in $WORKTREE on the new account (FRESH — no session to resume) ..."
fi
if ! "$TM" new-session -d -s "$SESS" -c "$WORKTREE" "$CMD"; then
  echo "ERROR: tmux new-session failed for '$SESS'. Lane is DOWN — relaunch manually:" >&2
  echo "       $TM new-session -d -s $SESS -c $WORKTREE \"$CMD\"" >&2
  exit 7
fi

# ── 6. Verify the re-token TOOK: auto-answer the resume menu, then require BOTH
#       the auth_fp flip AND a HEALTHY pane (op#12030 f/u). auth_fp alone is NOT
#       enough — a --resume on a large session parks at CC's resume menu with the
#       fp already stamped, so an fp-only "PASS" would hide a soft-wedged lane (and
#       a fleet-wide flip-all would park every big lane at once). ──────────────
echo "[switch_lane_token] verifying re-token (auto-answer resume menu + auth_fp flip + HEALTHY pane, up to ${POLL_S}s) ..."
DEADLINE=$(( $(date -u +%s) + POLL_S ))
AFTER_FP=""
MENU_ANSWERED=0
PANE_HEALTHY=0
LAST_PANE=""
while [ "$(date -u +%s)" -lt "$DEADLINE" ]; do
  sleep 3
  LAST_PANE="$("$TM" capture-pane -t "${SESS}:0.0" -p 2>/dev/null || true)"
  # Auto-answer CC's large-session resume menu ONCE so the lane doesn't park there.
  if [ "$MENU_ANSWERED" = 0 ] && resume_menu_present "$LAST_PANE"; then
    echo "[switch_lane_token] resume menu detected -> answering ${RESUME_MODE} (non-lossy default is full)"
    for _k in $(resume_menu_keys "$RESUME_MODE"); do
      "$TM" send-keys -t "${SESS}:0.0" "$_k"
      sleep 0.4
    done
    MENU_ANSWERED=1
    continue
  fi
  AFTER_FP="$(_fp_for_session "$SESS")"
  AFTER_FP="${AFTER_FP//[$'\t\r\n ']/}"
  if [ "$AFTER_FP" = "$NEW_FP" ] && pane_up_healthy "$LAST_PANE"; then
    PANE_HEALTHY=1
    break
  fi
done

# ── 6.5 Identity-stamped audit + break-glass alert (cai CAI-RESP-747 conds 2/3) ─
# The switch SCRIPT is the SINGLE execution rail every armed/break-glass re-token
# flows through (cai cond 1), and the console DB session is READ-ONLY, so THIS is
# the one place that can emit a complete, un-bypassable audit row: who / lane /
# from->to fp / break_glass flag / result / ts. On a BREAK-GLASS use (BREAK_GLASS=1,
# set by /api/switch-token) it escalates to a P1 ALERT so the exceptional path is
# never a silent routine bypass. Best-effort: a failed emit warns LOUDLY (stderr)
# but never fails an already-completed switch.
# PASS requires BOTH the auth_fp flip AND a healthy pane (op#12030 f/u): a lane
# parked at the resume menu has the right fp but is soft-wedged, which is a FAIL.
_SWITCH_RESULT="PASS"
if [ "$AFTER_FP" != "$NEW_FP" ] || [ "$PANE_HEALTHY" != "1" ]; then _SWITCH_RESULT="FAIL"; fi
SWITCH_RESULT="$_SWITCH_RESULT" \
SWITCH_ACTOR="${ACTOR:-cli}" SWITCH_BREAKGLASS="${BREAK_GLASS:-0}" SWITCH_ARMED="${ARMED:-0}" \
SWITCH_SESS="$SESS" SWITCH_BEFORE="${BEFORE_FP:-none}" SWITCH_AFTER="${AFTER_FP:-none}" \
SWITCH_TARGET="$NEW_FP" SWITCH_MODELAPPLY="$MODEL_APPLY" \
"$VENV_PY" - <<'PYAUDIT' 2>>/dev/stderr || echo "WARNING: switch audit/alert emit FAILED (switch itself already completed above)" >&2
import os
try:
    import psycopg
    # DATABASE_URL is already exported (the script did `set -a; source .env`).
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    bg = os.environ.get("SWITCH_BREAKGLASS") == "1"
    armed = os.environ.get("SWITCH_ARMED") == "1"
    kind = "MODEL-APPLY" if os.environ.get("SWITCH_MODELAPPLY") == "1" else "TOKEN-SWITCH"
    tag = ("BREAK-GLASS " + kind) if bg else (("ARMED " + kind) if armed else kind)
    who = os.environ.get("SWITCH_ACTOR", "cli")
    sess = os.environ.get("SWITCH_SESS"); before = os.environ.get("SWITCH_BEFORE")
    after = os.environ.get("SWITCH_AFTER"); target = os.environ.get("SWITCH_TARGET")
    res = os.environ.get("SWITCH_RESULT")
    subj = f"[{tag}] {sess}: {before}->{after} ({res}) by {who}"
    body = (f"{tag} on lane '{sess}' by {who}: account {before} -> {after} "
            f"(target {target}), result {res}. break_glass={bg}.")
    # LOUD 'blocker'/P1 for a break-glass use OR any FAIL (cai invariant 2: an
    # auth_fp mismatch after a switch must alert); a clean routine switch is a quiet
    # 'update'/P3 audit. (message_type is CHECK-constrained — 'blocker'/'update' valid.)
    loud = bg or res == "FAIL"
    mtype = "blocker" if loud else "update"
    prio = "P1" if loud else "P3"
    # from_agent must be a registered agent (FK) — the SRE tooling (cc-fleet-health)
    # emits the audit; the actual actor is stamped in the body ("by <who>").
    # Recipients: orch-console (Nazim) always; cai ALSO on ARMED or BREAK-GLASS
    # switches — the billing-affecting operator/emergency paths the governance node
    # needs visibility on (cai #16101, soft/non-blocking, requires_response=false).
    recipients = ["orch-console"] + (["cai"] if (armed or bg) else [])
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        for _to in recipients:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
                "VALUES ('cc-fleet-health',%s,%s,%s,%s,%s,false)",
                (_to, mtype, subj, body, prio))
        c.commit()
    print(f"[switch_lane_token] audit row emitted to {recipients} ({mtype}, {prio}).")
except Exception as e:
    raise SystemExit(f"audit emit error: {e}")
PYAUDIT

echo "───────────────────────────────────────────────────────────"
echo "  session:   $SESS"
echo "  BEFORE fp: ${BEFORE_FP:-<none>}"
echo "  AFTER  fp: ${AFTER_FP:-<not-yet-registered>}"
echo "  target fp: $NEW_FP"
if [ -n "$RESUME_ID" ]; then
  echo "  mode:      RESUMED conversation ($RESUME_ID)"
else
  echo "  mode:      FRESH (no prior session found to resume)"
fi
echo "  pane:      $( [ "$PANE_HEALTHY" = 1 ] && echo 'HEALTHY (composer ready / working)' || echo 'NOT healthy' )"
if [ "$AFTER_FP" = "$NEW_FP" ] && [ "$PANE_HEALTHY" = 1 ]; then
  echo "  RESULT:    PASS — lane re-tokened onto the new account AND came up healthy."
  echo "───────────────────────────────────────────────────────────"
  exit 0
elif [ "$AFTER_FP" = "$NEW_FP" ]; then
  # fp flipped but the pane never reached a healthy state within POLL_S — most
  # likely still PARKED at the resume menu (or crashed on boot). This is the
  # soft-wedge op#12030-f/u closes: LOUD, distinct exit — do NOT report "PASS".
  echo "  RESULT:    FAIL — re-tokened onto the new account BUT the pane is NOT healthy"
  echo "             within ${POLL_S}s (parked at the resume menu, or crashed on boot)."
  echo "             The lane is soft-wedged — inspect: $TM attach -t $SESS"
  echo "───────────────────────────────────────────────────────────"
  exit 10
else
  echo "  RESULT:    FAIL — auth_fp has not flipped within ${POLL_S}s."
  echo "             The relaunch may still be building context (auth_fp is stamped"
  echo "             late in launch_dangerous_cc.sh). Check: $TM attach -t $SESS"
  echo "───────────────────────────────────────────────────────────"
  exit 8
fi
