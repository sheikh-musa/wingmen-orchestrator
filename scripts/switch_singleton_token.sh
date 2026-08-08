#!/usr/bin/env bash
# switch_singleton_token.sh — RE-TOKEN a SINGLETON fleet node (cc-fleet-health, cai)
# onto a DIFFERENT Claude OAuth account, SAFELY.
#
# ── WHY a singleton-specific tool (and NOT switch_lane_token.sh) ──────────────
# switch_lane_token.sh re-tokens ENGINEER LANES by relaunching through
# launch_lane_as.sh -> launch_dangerous_cc.sh, whose identity is resolved by
# auto_agent_id — which allocates a SUB-TAG (e.g. cc-fleet-health-3) and wires
# engineer-lane machinery (code-push/Vercel/cc_work_sessions). For a SINGLETON
# that is WRONG and unsafe:
#   * cc-fleet-health MUST be agent_id 'cc-fleet-health' EXACTLY (no sub-tag),
#     with its SRE fleet_health_lease TAKE at boot + ANTHROPIC_API_KEY scrub.
#   * cai MUST be agent_id 'cai' EXACTLY, with its singleton identity guards.
# So singletons are relaunched through their OWN boot scripts (boot_fleet_health.sh
# / boot_cai.sh), never the lane launcher. That is what this tool does.
#
# ── HOW re-tokening works (same physics as switch_lane_token.sh) ──────────────
# A process's Claude account == the CLAUDE_CODE_OAUTH_TOKEN it launched with; it is
# FIXED at process launch and CANNOT be changed in place. So re-tokening == kill
# the tmux session + relaunch the node's boot script with
# CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE=$(cat <token-file>). Both boot scripts apply the
# override AFTER sourcing .env (so it wins) and `unset ANTHROPIC_API_KEY` (so the
# node bills to the Max subscription, not metered API). The token is read from the
# FILE inside the tmux shell via $(cat <file>) — it NEVER appears in argv/ps, only
# the file PATH does (identical discipline to launch_lane_as.sh). This tool prints
# only the sha256(token)[:12] fingerprint, never the token.
#
# ── PER-NODE re-token procedure ──────────────────────────────────────────────
#   cc-fleet-health : STATELESS monitor loop — a FRESH reboot loses nothing. Kill
#                     tmux 'fleet-health' + relaunch boot_fleet_health.sh with the
#                     override. boot re-TAKEs the fleet_health_lease + scrubs the key.
#   cai             : cai HOLDS conversational context — a bare kill would LOSE it.
#                     So this is CHECKPOINT-FIRST: cai must have written a FRESH
#                     restore point (reports/cai-handoff-NOW.md, exactly what
#                     reset_cai.sh requires) BEFORE the switch. We refuse unless that
#                     handoff exists and is fresh; then kill + relaunch boot_cai.sh
#                     with the override, and fresh-cai reconstitutes from the handoff
#                     (CLAUDE.md boot SQL + the handoff = the authority). NOTE:
#                     reset_cai.sh itself does an IN-PLACE /clear (same process, so
#                     it does NOT re-token) — re-tokening REQUIRES a new process, so
#                     the safety here is the handoff-freshness gate, not /clear.
#
# ── launchd note (checked 2026-08-05) ────────────────────────────────────────
# NEITHER singleton is launchd-KeepAlive-managed, so there is no job to revert the
# token after the kill. The plist 'dev.wingmen.fleet-health' is a RED HERRING: it
# runs scripts/fleet_health.py --quiet on a 600s StartInterval (a periodic DB-vs-
# tmux reconcile) — it is NOT the SRE tmux node and sets no token. Both singletons
# run as operator-booted tmux sessions (fleet-health, cai) on /usr/local/bin/tmux.
# If that ever changes (a KeepAlive supervisor appears), this tool must be driven
# via launchctl bootout/kickstart with the override in the job env instead.
#
# Usage:  scripts/switch_singleton_token.sh [--dry-run] [--force] <node> <token-file>
#   node ∈ { cc-fleet-health, cai }
#   e.g.  scripts/switch_singleton_token.sh --dry-run cc-fleet-health ~/.wingmen/keys/musa-oauth-token
#         scripts/switch_singleton_token.sh cc-fleet-health ~/.wingmen/keys/musa-oauth-token
#         scripts/switch_singleton_token.sh cai ~/.wingmen/keys/musa-oauth-token
#
# Fingerprints (2026-08-05): musa-oauth-token 68142948c003 · syed-oauth-token 582043088eae
set -uo pipefail
cd "$HOME/wingmen/orchestrator" || { echo "ERROR: orch dir missing" >&2; exit 9; }
ORCH_DIR="$(pwd)"
set -a; source .env 2>/dev/null || true; set +a

_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# composer_capture.sh gives us pane_busy (busy-guard). Optional: if missing we warn
# and proceed WITHOUT the busy guard rather than failing closed on a helper.
HAVE_BUSY=0
if [ -r "$_LIB/composer_capture.sh" ]; then . "$_LIB/composer_capture.sh" && HAVE_BUSY=1; fi

TM="${TM:-/usr/local/bin/tmux}"
[ -x "$TM" ] || TM="$(command -v tmux || echo /usr/local/bin/tmux)"
FORBIDDEN_BASENAME="gazzabyte-oauth-token"
# Alias-proof forbidden-fp set (op#10851 f/u): refuse a token by its CONTENT
# fingerprint even if aliased under an innocent filename (matches the console guard).
FORBIDDEN_FPS="13589de86f29"
EMPTY_HASH="e3b0c44298fc"                       # sha256("")[:12] — empty token file
POLL_S="${SWITCH_POLL_S:-60}"
CAI_HANDOFF_MAX_AGE_MIN="${CAI_HANDOFF_MAX_AGE_MIN:-45}"  # cai checkpoint freshness

# ── arg parse: [--dry-run] [--force] <node> <token-file> ─────────────────────
DRY=0
FORCE="${SWITCH_FORCE:-0}"
NODE=""
TOKFILE=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --force)   FORCE=1 ;;
    *) if [ -z "$NODE" ]; then NODE="$a"; elif [ -z "$TOKFILE" ]; then TOKFILE="$a"; fi ;;
  esac
done
_usage() { echo "usage: switch_singleton_token.sh [--dry-run] [--force] <cc-fleet-health|cai> <token-file>" >&2; }
[ -n "$NODE" ]    || { _usage; exit 2; }
[ -n "$TOKFILE" ] || { _usage; exit 2; }

# ── NODE REGISTRY: node -> tmux session, boot script, agent_id, dir, checkpoint ─
# launchd_managed is 'no' for both (see header). checkpoint='handoff:<path>' means
# CHECKPOINT-FIRST: the restore point must exist + be fresh before we touch it.
case "$NODE" in
  cc-fleet-health)
    SESS="fleet-health"
    AGENT_ID="cc-fleet-health"
    NODE_DIR="$HOME/wingmen/fleet-health"
    BOOT="$NODE_DIR/boot_fleet_health.sh"
    CHECKPOINT=""                                 # stateless — fresh reboot is fine
    ;;
  cai)
    SESS="cai"
    AGENT_ID="cai"
    NODE_DIR="$HOME/wingmen/wingmen-cai"
    BOOT="$NODE_DIR/boot_cai.sh"
    CHECKPOINT="$NODE_DIR/reports/cai-handoff-NOW.md"  # context-preserving restore point
    ;;
  *)
    echo "ERROR: unknown singleton '$NODE' — must be one of: cc-fleet-health, cai" >&2
    _usage; exit 2 ;;
esac
PANE="${SESS}:0.0"

# ── 1. Token-file validation (fail-closed, loud) ─────────────────────────────
[ -r "$TOKFILE" ] || { echo "ERROR: token file not readable: $TOKFILE" >&2; exit 3; }
# GOVERNANCE gate (CAI-729): the gazzabyte consumer token is FORBIDDEN for fleet
# nodes. Check both the given path AND its realpath (a symlink cannot smuggle it in).
_bn="$(basename "$TOKFILE")"
_rp="$(cd "$(dirname "$TOKFILE")" 2>/dev/null && pwd -P)/$_bn"
_rpbn="$(basename "$_rp")"
if [ "$_bn" = "$FORBIDDEN_BASENAME" ] || [ "$_rpbn" = "$FORBIDDEN_BASENAME" ]; then
  echo "ERROR: '$FORBIDDEN_BASENAME' is a CONSUMER Max token FORBIDDEN for fleet use (CAI-729). Refusing." >&2
  exit 4
fi
# Absolute token path for the relaunch command (so the tmux shell can $(cat) it).
TOKFILE_ABS="$(cd "$(dirname "$TOKFILE")" && pwd -P)/$_bn"

# TARGET fingerprint — EXACTLY as the boot scripts derive it: the override is
# $(cat <file>), which strips the trailing newline; sha256 of that, first 12 hex.
TARGET_FP="$(printf '%s' "$(cat "$TOKFILE_ABS")" | shasum -a 256 2>/dev/null | cut -c1-12)"
if [ -z "$TARGET_FP" ] || [ "$TARGET_FP" = "$EMPTY_HASH" ]; then
  echo "ERROR: token file is empty/unreadable (fp resolves to the empty-string hash). Refusing." >&2
  exit 4
fi
# Alias-proof forbidden-fp guard (op#10851 f/u): refuse the token by CONTENT fp.
case " $FORBIDDEN_FPS " in
  *" $TARGET_FP "*)
    echo "ERROR: token fingerprint $TARGET_FP is a FORBIDDEN consumer token (CAI-729), whatever its filename. Refusing." >&2
    exit 4 ;;
esac

# ── 2. Boot-script sanity + Max-billing (ANTHROPIC_API_KEY) guard ────────────
[ -r "$BOOT" ] || { echo "ERROR: boot script missing/unreadable: $BOOT" >&2; exit 3; }
# The node bills to Max ONLY if its process env has NO ANTHROPIC_API_KEY (a present
# key forces metered API billing). Both boot scripts `unset ANTHROPIC_API_KEY`;
# refuse if that scrub line is gone (defense in depth — a rewritten boot could leak
# .env's ANTHROPIC_API_KEY into the relaunched node and silently meter it).
if ! grep -Eq '^[[:space:]]*unset[[:space:]]+ANTHROPIC_API_KEY' "$BOOT"; then
  echo "ERROR: $BOOT does not 'unset ANTHROPIC_API_KEY' — relaunch could bill METERED API. Refusing." >&2
  exit 4
fi

# ── 3. tmux + descendant-env fingerprint helpers ─────────────────────────────
# Singletons do NOT reliably stamp agent_status.auth_fp, so we VERIFY the account
# by reading the LIVE claude process env (never printing the token): walk the pane's
# descendants, find the one carrying CLAUDE_CODE_OAUTH_TOKEN, and fingerprint it.
_descendants() { local p="$1"; printf '%s\n' "$p"; local c; for c in $(pgrep -P "$p" 2>/dev/null); do _descendants "$c"; done; }

_env_fp_for_session() {   # echoes sha256(CLAUDE_CODE_OAUTH_TOKEN)[:12] of the node's claude, or nothing
  local sess="$1" ppid pid fp
  ppid="$("$TM" display-message -p -t "$sess" '#{pane_pid}' 2>/dev/null)" || return 0
  [ -n "$ppid" ] || return 0
  for pid in $(_descendants "$ppid"); do
    # -f2- (not -f2) keeps the whole value even if a token ever contains '='.
    fp="$(ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep '^CLAUDE_CODE_OAUTH_TOKEN=' | head -1 | cut -d= -f2- | shasum -a 256 2>/dev/null | cut -c1-12)"
    if [ -n "$fp" ] && [ "$fp" != "$EMPTY_HASH" ]; then printf '%s\n' "$fp"; return 0; fi
  done
  return 0
}

_anthropic_key_leaked() {  # echoes 'yes' if any node descendant carries a non-empty ANTHROPIC_API_KEY
  local sess="$1" ppid pid v
  ppid="$("$TM" display-message -p -t "$sess" '#{pane_pid}' 2>/dev/null)" || return 0
  [ -n "$ppid" ] || return 0
  for pid in $(_descendants "$ppid"); do
    v="$(ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep '^ANTHROPIC_API_KEY=' | head -1 | cut -d= -f2-)"
    if [ -n "$v" ]; then printf 'yes\n'; return 0; fi
  done
  return 0
}

# ── 4. Resolve live session + BEFORE fingerprint ─────────────────────────────
if ! "$TM" has-session -t "=$SESS" 2>/dev/null; then
  echo "ERROR: tmux session '$SESS' not found on this host ($TM). Node '$NODE' is not running here." >&2
  exit 1
fi
WORKTREE="$("$TM" display-message -p -t "$SESS" '#{pane_current_path}' 2>/dev/null)"
BEFORE_FP="$(_env_fp_for_session "$SESS")"

echo "[switch_singleton] node=$NODE  agent_id=$AGENT_ID  tmux=$SESS  dir=$NODE_DIR"
echo "[switch_singleton] boot=$BOOT"
echo "[switch_singleton] BEFORE fp=${BEFORE_FP:-<unreadable>}  ->  target fp=$TARGET_FP"
[ -n "$WORKTREE" ] && [ "$WORKTREE" != "$NODE_DIR" ] && \
  echo "[switch_singleton] note: live pane cwd is $WORKTREE (registry dir is $NODE_DIR)"

# Idempotent short-circuit: already on the target account -> no restart.
if [ -n "$BEFORE_FP" ] && [ "$BEFORE_FP" = "$TARGET_FP" ]; then
  echo "[switch_singleton] node is ALREADY on target account ($TARGET_FP) — no restart. PASS (no-op)."
  exit 0
fi

# ── 5. cai CHECKPOINT-FIRST gate (context preservation) ──────────────────────
CHECKPOINT_NOTE="n/a (stateless node — fresh reboot loses nothing)"
if [ -n "$CHECKPOINT" ]; then
  if [ ! -f "$CHECKPOINT" ]; then
    CHECKPOINT_NOTE="MISSING restore point ($CHECKPOINT)"
    echo "ERROR: cai restore point missing: $CHECKPOINT" >&2
    echo "       cai holds CONTEXT — re-tokening requires a fresh process, which LOSES it unless" >&2
    echo "       cai has checkpointed first. Have cai write reports/cai-handoff-NOW.md, THEN re-run." >&2
    # In dry-run we PREVIEW (report + continue to the plan); a live run REFUSES here.
    [ "$DRY" = "1" ] || exit 6
  else
    _mtime="$(stat -f %m "$CHECKPOINT" 2>/dev/null || echo 0)"
    _age_min=$(( ( $(date -u +%s) - _mtime ) / 60 ))
    if [ "$_age_min" -gt "$CAI_HANDOFF_MAX_AGE_MIN" ]; then
      if [ "$FORCE" = "1" ]; then
        echo "WARNING: cai restore point is STALE (${_age_min}m > ${CAI_HANDOFF_MAX_AGE_MIN}m) — --force set, proceeding." >&2
        echo "WARNING: fresh-cai will reconstitute from a ${_age_min}m-old handoff; recent context will be LOST." >&2
        CHECKPOINT_NOTE="STALE handoff (${_age_min}m) — forced"
      else
        CHECKPOINT_NOTE="STALE handoff (${_age_min}m > ${CAI_HANDOFF_MAX_AGE_MIN}m) — live run WOULD REFUSE (need fresh handoff or --force)"
        echo "ERROR: cai restore point is STALE — $CHECKPOINT is ${_age_min}m old (> ${CAI_HANDOFF_MAX_AGE_MIN}m)." >&2
        echo "       Have cai write a FRESH reports/cai-handoff-NOW.md, then re-run (or --force to accept the loss)." >&2
        # In dry-run we PREVIEW (report + continue to the plan); a live run REFUSES here.
        [ "$DRY" = "1" ] || exit 6
      fi
    else
      CHECKPOINT_NOTE="fresh handoff (${_age_min}m old) at $CHECKPOINT"
    fi
  fi
fi

# ── 6. Busy guard (reuse composer_capture pane_busy) ─────────────────────────
BUSY_NOTE="not checked (composer_capture.sh unavailable)"
if [ "$HAVE_BUSY" = "1" ]; then
  pane_busy "$TM" "$PANE"
  if [ "${CC_BUSY_STALE:-0}" = 1 ]; then
    echo "WARNING: '$SESS' shows a background-agent marker but the pane is FROZEN (byte-identical)." >&2
    echo "         Treating as NOT busy: a live wait animates." >&2
  fi
  if [ "${CC_BUSY:-0}" = 1 ]; then
    BUSY_NOTE="BUSY — ${CC_BUSY_REASON:-in-flight}"
    if [ "$FORCE" = "1" ]; then
      echo "WARNING: '$SESS' is BUSY — ${CC_BUSY_REASON:-in-flight} — --force set, re-tokening ANYWAY (in-flight work discarded)." >&2
    elif [ "$DRY" != "1" ]; then
      echo "ERROR: '$SESS' is BUSY — ${CC_BUSY_REASON:-in-flight} — refusing to re-token. Pass --force to override." >&2
      exit 5
    fi
  else
    BUSY_NOTE="idle"
  fi
fi

# ── 7. Build the relaunch command (mirrors the node's original pane_start_command) ─
# The token is read from the FILE inside the tmux shell via $(cat <file>) — it never
# appears in argv/ps, only the PATH does. %s inserts literal paths; the $(...) and
# quotes are LITERAL in the format string so the tmux shell (/bin/sh -c) evaluates
# $(cat ...) at launch, exactly as the node was originally booted.
printf -v RELAUNCH_CMD 'CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE="$(cat %s)" exec %s' "$TOKFILE_ABS" "$BOOT"

# ── 8. DRY-RUN: print the plan and STOP (no kill, no relaunch) ────────────────
if [ "$DRY" = "1" ]; then
  echo "───────────────────────────────────────────────────────────"
  echo "  DRY-RUN — would perform (nothing changed):"
  echo "  node:        $NODE  (agent_id=$AGENT_ID)"
  echo "  BEFORE fp:   ${BEFORE_FP:-<unreadable>}"
  echo "  target fp:   $TARGET_FP"
  echo "  checkpoint:  $CHECKPOINT_NOTE"
  echo "  busy:        $BUSY_NOTE"
  echo "  launchd:     not KeepAlive-managed — raw tmux kill+relaunch is safe"
  echo "  1) $TM kill-session -t $SESS"
  echo "  2) $TM new-session -d -s $SESS -c $NODE_DIR '$RELAUNCH_CMD'"
  echo "  3) poll live process env (up to ${POLL_S}s) until CLAUDE_CODE_OAUTH_TOKEN fp == $TARGET_FP"
  echo "  (token read from $TOKFILE_ABS inside the tmux shell — never printed, never in argv)"
  echo "───────────────────────────────────────────────────────────"
  exit 0
fi

# ── 9. KILL + RELAUNCH on the new account ────────────────────────────────────
if [ -n "$CHECKPOINT" ]; then
  echo "⚠ RE-TOKEN kills '$SESS' and relaunches FRESH. cai reconstitutes from its handoff"
  echo "  ($CHECKPOINT). The live in-flight turn (if any) is interrupted."
else
  echo "⚠ RE-TOKEN kills '$SESS' and relaunches FRESH. cc-fleet-health is a stateless monitor,"
  echo "  so nothing is lost; it re-TAKEs the fleet_health_lease at boot."
fi
echo "[switch_singleton] killing '$SESS' ..."
"$TM" kill-session -t "$SESS" 2>/dev/null || true
sleep 1
echo "[switch_singleton] relaunching '$SESS' via $(basename "$BOOT") on the new account ..."
if ! "$TM" new-session -d -s "$SESS" -c "$NODE_DIR" "$RELAUNCH_CMD"; then
  echo "ERROR: tmux new-session failed for '$SESS'. Node is DOWN — relaunch manually:" >&2
  echo "       $TM new-session -d -s $SESS -c $NODE_DIR '$RELAUNCH_CMD'" >&2
  exit 7
fi

# ── 10. Poll the live process env until the fp flips to target (up to POLL_S) ─
echo "[switch_singleton] verifying token flip via live process env (polling up to ${POLL_S}s) ..."
DEADLINE=$(( $(date -u +%s) + POLL_S ))
AFTER_FP=""
while [ "$(date -u +%s)" -lt "$DEADLINE" ]; do
  sleep 3
  AFTER_FP="$(_env_fp_for_session "$SESS")"
  [ "$AFTER_FP" = "$TARGET_FP" ] && break
done
LEAK="$(_anthropic_key_leaked "$SESS")"

# ── 11. Boxed PASS/FAIL summary ──────────────────────────────────────────────
echo "───────────────────────────────────────────────────────────"
echo "  node:        $NODE  (agent_id=$AGENT_ID, tmux=$SESS)"
echo "  BEFORE fp:   ${BEFORE_FP:-<unreadable>}"
echo "  AFTER  fp:   ${AFTER_FP:-<not-yet-registered>}"
echo "  target fp:   $TARGET_FP"
echo "  checkpoint:  $CHECKPOINT_NOTE"
if [ "$LEAK" = "yes" ]; then
  echo "  billing:     ⚠ ANTHROPIC_API_KEY PRESENT in node env — METERED API billing risk!"
fi
if [ "$AFTER_FP" = "$TARGET_FP" ] && [ "$LEAK" != "yes" ]; then
  echo "  RESULT:      PASS — $NODE re-tokened onto $TARGET_FP (billing to Max)."
  echo "───────────────────────────────────────────────────────────"
  exit 0
elif [ "$AFTER_FP" = "$TARGET_FP" ] && [ "$LEAK" = "yes" ]; then
  echo "  RESULT:      FAIL — token flipped but ANTHROPIC_API_KEY leaked into the node env."
  echo "               Inspect $BOOT's 'unset ANTHROPIC_API_KEY' + .env; the node is metering."
  echo "───────────────────────────────────────────────────────────"
  exit 8
else
  echo "  RESULT:      FAIL — token fp has not flipped within ${POLL_S}s."
  echo "               The relaunch may still be starting (claude env is set late). Check:"
  echo "               $TM attach -t $SESS"
  echo "───────────────────────────────────────────────────────────"
  exit 8
fi
