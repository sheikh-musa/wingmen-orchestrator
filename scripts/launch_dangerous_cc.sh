#!/usr/bin/env bash
# scripts/launch_dangerous_cc.sh
# ARCH-022 Layer 2 — Wrapper for dangerous-mode CC session launches.
#
# Run this instead of raw: claude --dangerously-skip-permissions
#
# What this script does:
#   1. Runs agent_boot (read inbox + context, mark heartbeat)
#   2. Starts a background heartbeat loop (every 5 min, auto-killed on exit)
#   3. Prints a CHECK-IN reminder every 25 min (ARCH-022 amendment)
#   4. Installs EXIT trap: writes cc_work_sessions row + posts agent_message
#   5. Launches claude --dangerously-skip-permissions in the caller's directory
#
# Usage:
#   ./scripts/launch_dangerous_cc.sh
#   ./scripts/launch_dangerous_cc.sh --repo orchestrator
#   ./scripts/launch_dangerous_cc.sh -- --resume <session-id>
#
# Environment:
#   CC_REPO      — repo name override (default: caller pwd's git toplevel basename)
#   MODEL        — claude model override (default: claude-opus-4-8).
#                  Opt-in to claude-fable-5 ($10/$50 per MTok, 2x Opus) via
#                  MODEL=claude-fable-5 for genuinely hard long-horizon work.
#                  Note (per CAI-RESP-192 amendment): Fable 5 blocks cyber-
#                  security/biology and falls back to Opus 4.8 there — do
#                  not route BUG-024 / RLS / identity hardening via Fable 5.
#
# Identity (GOVERNANCE-CLEANUP-001 Step 3, composes msgs 315/317/324):
#   Base family (CC_BASE_AGENT_ID) resolved from pwd → data-driven family map
#   built from agents.repo_scope at launch time (delta-v2: no hardcoded
#   constant; worktrees handled via git-toplevel + suffix-strip). Unrecognized
#   pwd = fail-fast ABORT. Sub-tag (CC_AGENT_ID) allocated via bounded
#   pg_try_advisory_xact_lock (5s retry) + scan of agent_status, picks the
#   smallest free N in the family. GUC + agent_status = sub-tag.
#   agent_messages.from_agent = base (FK requires a registered agents.id row).
#   Sub-identity promotion to first-class FK is Step 4 (BUG-024 Phase 1).

set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
CALLER_DIR="$(pwd)"

# ── CLI arg parse (Step 3: --repo override, -- passthrough to claude) ────────
# Single-pass parser — respects `--` boundary (plan's two-pass parser had a
# bug where `./launch.sh -- --repo X` would let the second pass steal the
# `--repo X` from claude's passthrough).

REPO_OVERRIDE=""
CLAUDE_PASSTHROUGH=()
PASS_THROUGH=false
SCHEDULED_PROMPT_FILE=""
while [ $# -gt 0 ]; do
    if $PASS_THROUGH; then
        CLAUDE_PASSTHROUGH+=("$1")
        shift
        continue
    fi
    case "$1" in
        --repo=*)
            REPO_OVERRIDE="${1#--repo=}"
            shift
            ;;
        --repo)
            shift
            REPO_OVERRIDE="${1:-}"
            [ $# -gt 0 ] && shift
            ;;
        --scheduled-prompt=*)
            # CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 3: route into
            # bounded non-interactive scheduled-sweep mode. Path is relative
            # to ORCH_DIR (e.g. skills/scheduled-sweep-prompt.md).
            SCHEDULED_PROMPT_FILE="${1#--scheduled-prompt=}"
            shift
            ;;
        --scheduled-prompt)
            shift
            SCHEDULED_PROMPT_FILE="${1:-}"
            [ $# -gt 0 ] && shift
            ;;
        --)
            PASS_THROUGH=true
            shift
            ;;
        *)
            # Anything else before `--` is ignored (no bare positional args).
            shift
            ;;
    esac
done

# Repo name: --repo flag > CC_REPO env > pwd git basename
REPO_NAME="${REPO_OVERRIDE:-${CC_REPO:-}}"
if [ -z "$REPO_NAME" ]; then
    REPO_NAME="$(git -C "$CALLER_DIR" rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || echo "unknown")"
fi

SESSION_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SESSION_START_EPOCH="$(date -u +%s)"
HEARTBEAT_PID=""

# ── Step 3: dual-identity resolution via scripts/lib/auto_agent_id ───────────

# Load DATABASE_URL from .env for the helper call.
# Note: sourcing with `set -a` marks NEW assignments for export and OVERWRITES
# same-named shell vars — so .env wins over pre-set shell env. This is
# intentional for the orchestrator-managed launcher (canonical config lives
# in .env, not the operator's shell).
# shellcheck disable=SC1091
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo -e "\033[31mERROR: DATABASE_URL not set — cannot allocate agent identity\033[0m" >&2
    echo "       Add DATABASE_URL=postgres://... to $ORCH_DIR/.env" >&2
    exit 1
fi

ALLOC_JSON="$(cd "$ORCH_DIR" && "$VENV_PY" -m scripts.lib.auto_agent_id \
    --pwd "$CALLER_DIR" \
    --repo "$REPO_NAME" \
    --dsn "$DSN" 2>/tmp/cc_alloc_err.log)" || {
    echo -e "\033[31mERROR: identity allocation failed\033[0m" >&2
    cat /tmp/cc_alloc_err.log >&2
    # A4 (CAI-RESP-053): surface the most-recent LockTimeout diagnostic.
    # allocate_sub_tag_and_register flushes a fsync'd forensic file before
    # raising — co-locate its tail with the alloc-err tail for consistency.
    RECENT_LOCK_DIAG=$(ls -t /tmp/cc_lock_timeout_*.log 2>/dev/null | head -n 1 || true)
    if [ -n "$RECENT_LOCK_DIAG" ] && [ -f "$RECENT_LOCK_DIAG" ]; then
        echo -e "\033[33m  Most recent lock-timeout diagnostic: ${RECENT_LOCK_DIAG}\033[0m" >&2
        tail -n 20 "$RECENT_LOCK_DIAG" | sed 's/^/    /' >&2
    fi
    exit 1
}

CC_AGENT_ID="$(echo "$ALLOC_JSON" | "$VENV_PY" -c 'import sys,json;print(json.load(sys.stdin)["sub_tag"])')"
CC_BASE_AGENT_ID="$(echo "$ALLOC_JSON" | "$VENV_PY" -c 'import sys,json;print(json.load(sys.stdin)["base"])')"
# Delta-v2 non-load-bearing #4: overlap_warnings is list of [aid, age_s]
# pairs. Format each as "aid (Ns ago)" for operator-readable output.
OVERLAP_WARNINGS="$(echo "$ALLOC_JSON" | "$VENV_PY" -c 'import sys,json;print(", ".join(f"{a} ({s}s ago)" for a,s in json.load(sys.stdin)["overlap_warnings"]))')"

# Guard: if the helper returned exit-0 but produced unparseable stdout, the
# json.load calls above still `print("")` via silent failure and we'd proceed
# with empty agent_id — which would then land malformed agent_status rows.
# Fail-loud here so the operator sees the real problem before claude starts.
if [ -z "$CC_AGENT_ID" ] || [ -z "$CC_BASE_AGENT_ID" ]; then
    echo -e "\033[31mERROR: auto_agent_id returned empty sub_tag or base_agent_id\033[0m" >&2
    echo "       Raw JSON: $ALLOC_JSON" >&2
    exit 1
fi

export CC_AGENT_ID
export CC_BASE_AGENT_ID
export SCOPE_REPO="$REPO_NAME"

# Legacy alias: some blocks below still reference $AGENT_ID. Retain a local
# only for readability; every write site specifies sub-tag vs base explicitly.
AGENT_ID="$CC_AGENT_ID"
BASE_AGENT_ID="$CC_BASE_AGENT_ID"

# ── Colours ───────────────────────────────────────────────────────────────────

BOLD='\033[1m'
TEAL='\033[36m'
AMBER='\033[33m'
RED='\033[31m'
DIM='\033[2m'
RESET='\033[0m'

# ── Python helper — inline DB operations ──────────────────────────────────────

_py() {
    # Run a Python snippet in the orchestrator venv
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
$1
" 2>/dev/null || true
}

# ── 1. Print header ───────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${TEAL}╔══════════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${TEAL}║   WINGMEN AGENT BOOT — ARCH-022 Layer 2 + Step 3 multi-repo         ║${RESET}"
echo -e "${BOLD}${TEAL}╚══════════════════════════════════════════════════════════════════════╝${RESET}"
echo -e "${DIM}Sub-tag: ${CC_AGENT_ID}  |  Base: ${CC_BASE_AGENT_ID}  |  Repo: ${REPO_NAME}${RESET}"
echo -e "${DIM}Started: ${SESSION_START}  |  pwd: ${CALLER_DIR}${RESET}"
if [ -n "$OVERLAP_WARNINGS" ]; then
    echo -e "${AMBER}${BOLD}⚠ OVERLAP: family siblings in ${REPO_NAME}: ${OVERLAP_WARNINGS}${RESET}"
    echo -e "${AMBER}  Coordinate scope or split work before editing shared paths.${RESET}"
fi
echo ""

# ── 2. Build session context block ───────────────────────────────────────────
# Queries Supabase for unread messages, agent context, and in-scope governance
# decisions. Marks messages as read and bumps agent heartbeat.
# The block is passed as -p to claude so it arrives as the initial user message
# with Musa's authority (assembled before launch, not at runtime).
#
# NOTE: `claude -p "..."` launches a non-interactive session — claude processes
# the prompt and exits. If you want an interactive session, launch normally
# and paste the context manually, or use `claude --resume` with a prior session.

echo -e "${BOLD}▶ Building session context for ${CC_BASE_AGENT_ID}...${RESET}"
# Stdout = context block (captured). Stderr = diagnostics (shown on terminal).
#
# Delta-v2 L3-A1 fix: pass CC_BASE_AGENT_ID, NOT CC_AGENT_ID. The context
# builder is per-FAMILY, not per-instance:
#   - scripts/build_launch_context.py L57 — agent_context.eq('agent_id', base)
#   - scripts/build_launch_context.py L111 — inbox filter to_agent.eq.{base}
#   - scripts/build_launch_context.py L187-189 — agents.update(...).eq('id', base)
# Passing the sub-tag would land an empty agent_context row + hidden inbox
# (sibling filter would match literal 'cc-ihsanos-3' while all inbox rows are
# addressed to base 'cc-ihsanos' with '[cc-ihsanos-3]' tagged in body).
LAUNCH_CONTEXT="$(cd "$ORCH_DIR" && "$VENV_PY" -m scripts.build_launch_context --agent "$CC_BASE_AGENT_ID")" || {
    echo -e "${AMBER}⚠ build_launch_context failed. Continuing without injected context.${RESET}"
    LAUNCH_CONTEXT=""
}

if [ -n "$LAUNCH_CONTEXT" ]; then
    echo -e "${TEAL}  Context assembled: $(echo "$LAUNCH_CONTEXT" | wc -l | tr -d ' ') lines, $(echo -n "$LAUNCH_CONTEXT" | wc -c | tr -d ' ') chars${RESET}"
else
    echo -e "${AMBER}  No context to inject.${RESET}"
fi
echo ""

# ── 2.5 ARCH-035 — agent_status already UPSERTed by auto_agent_id helper ─────
# The helper acquired the advisory lock, scanned siblings, picked sub_tag,
# and UPSERTed agent_status(sub_tag, status=working, current_task=session-launch,
# scope_repos=[REPO_NAME]) all in one TX with GUC=sub_tag. Nothing to do here.

echo -e "${BOLD}▶ agent_status registered: ${CC_AGENT_ID} (scope_repos=[${REPO_NAME}])${RESET}"
echo ""

# ── 3. Start background heartbeat loop ────────────────────────────────────────

_heartbeat_loop() {
    # Three heartbeats on a 5-minute cadence:
    #   1. agents.last_heartbeat (base id, legacy agents table) — FAIL LOUD (A2).
    #      Feeds stale_agents view + telegram /status + operator dashboards;
    #      silent failure = operator blindness. Stderr appends to a tailable
    #      size-capped log.
    #   2. agent_status.last_heartbeat (sub-tag, ARCH-035 with GUC) — best-effort.
    #      The stale_agents 15-min view-based backstop covers silent failure here.
    #   3. ~/.wingmen/cc_active marker file (CAI-RESP-114) — touched every tick
    #      so ai_provider._yield_if_interactive_active sees a fresh mtime and
    #      pauses background CLI spawns while interactive sessions are alive.
    #      Per CAI-PROCESS-MAX-FIRST-001 (d) Mitigation 2 — substrate was missing
    #      this side because no one wrote the marker. Standalone fix authorized
    #      in CAI-RESP-114; pre-stage so Phase 3 yield works post-Eid bootstrap.
    mkdir -p "$HOME/.wingmen" 2>/dev/null || true
    : > "$HOME/.wingmen/cc_active"
    local HB_ERR_LOG="/tmp/cc_heartbeat_err.log"
    local HB_ERR_CAP=10485760  # 10 MB cap (CAI-RESP-053 sub-amendment)
    while true; do
        sleep 300
        # Refresh interactive-session marker mtime. Empty file is fine — yield
        # mechanism only checks mtime, not content. `touch` would also work.
        : > "$HOME/.wingmen/cc_active" 2>/dev/null || true
        # SA1 (CAI-RESP-054): rotate the heartbeat err log BEFORE truncating on
        # cap breach — error-storm is the failure mode the log exists for, and
        # truncate-in-place loses the last 10MB of evidence at that moment.
        # One rotation generation (.1) bounds total footprint to ~20MB.
        if [ -f "$HB_ERR_LOG" ] && [ "$(wc -c < "$HB_ERR_LOG" 2>/dev/null || echo 0)" -gt "$HB_ERR_CAP" ]; then
            mv -f "$HB_ERR_LOG" "${HB_ERR_LOG}.1" 2>/dev/null || true
            echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [heartbeat-log] rotated (prev → ${HB_ERR_LOG}.1, cap=$HB_ERR_CAP B)" > "$HB_ERR_LOG"
        fi
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
from datetime import datetime, timezone
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
now_iso = datetime.now(timezone.utc).isoformat()
# agents table — base id (FK-enforced)
sb.table('agents').update({'last_heartbeat': now_iso}).eq('id', '$BASE_AGENT_ID').execute()
" 2>>"$HB_ERR_LOG"
        # agent_status heartbeat needs psycopg (GUC).
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.exit(0)
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)
try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('$AGENT_ID',))
            cur.execute(\"UPDATE agent_status SET last_heartbeat=now(), updated_at=now() WHERE agent_id=%s\", ('$AGENT_ID',))
        conn.commit()
except Exception:
    pass
" 2>/dev/null || true
    done
}

_heartbeat_loop &
HEARTBEAT_PID=$!

# ── 4. Vercel deployment verification ─────────────────────────────────────────
# Operator-decision (2026-04-30): the prior CHECK-IN reminder loop and
# UNPUSHED-COMMITS warning fired every 25 min in the session. Removed —
# CCs can't actually "check in" autonomously, so the banner was pure noise.
# Operator can run `git status` directly when they want push state.
# Call after every git push to confirm Vercel reaches READY or ERROR state.
# Requires VERCEL_TOKEN + VERCEL_TEAM_ID in orchestrator .env (already present).
# Falls back gracefully if token is missing.

_verify_vercel_deploy() {
    local repo_dir="${1:-$CALLER_DIR}"
    local commit_sha
    commit_sha="$(git -C "$repo_dir" rev-parse HEAD 2>/dev/null)"

    # Load Vercel creds from orchestrator .env
    local token team_id project_id
    token="$(grep -E '^VERCEL_TOKEN=' "$ORCH_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' | sed 's/#.*//')"
    team_id="$(grep -E '^VERCEL_TEAM_ID=' "$ORCH_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' | sed 's/#.*//')"

    # Try to get project ID from caller repo's .vercel/project.json
    local project_json="$repo_dir/.vercel/project.json"
    if [ -f "$project_json" ]; then
        project_id="$(python3 -c "import json,sys; d=json.load(open('$project_json')); print(d.get('projectId',''))" 2>/dev/null)"
    fi

    if [ -z "$token" ] || [ -z "$project_id" ]; then
        echo -e "${AMBER}⚠ Vercel token or project ID not available — skipping deploy check${RESET}"
        return 0
    fi

    echo -e "${BOLD}▶ Vercel deploy check for commit ${commit_sha:0:8}...${RESET}"

    local deadline=$(( $(date -u +%s) + 300 ))  # 5-minute timeout
    local state="" deploy_url="" build_log_url=""
    local poll_count=0

    # Brief initial wait for Vercel to pick up the push
    sleep 20

    while [ "$(date -u +%s)" -lt "$deadline" ]; do
        poll_count=$(( poll_count + 1 ))

        local api_url="https://api.vercel.com/v6/deployments?projectId=${project_id}&limit=5&target=production"
        [ -n "$team_id" ] && api_url="${api_url}&teamId=${team_id}"

        local response
        response="$(curl -sf -H "Authorization: Bearer ${token}" "$api_url" 2>/dev/null)"

        if [ -z "$response" ]; then
            echo -e "${DIM}  poll ${poll_count}: API unreachable, retrying in 15s...${RESET}"
            sleep 15
            continue
        fi

        # Find deployment matching current commit SHA
        state="$(python3 -c "
import json, sys
data = json.loads('''$response''')
deployments = data.get('deployments', [])
for d in deployments:
    meta = d.get('meta', {})
    if meta.get('githubCommitSha', '').startswith('${commit_sha:0:8}') or d.get('meta', {}).get('githubCommitSha') == '${commit_sha}':
        print(d.get('state', 'UNKNOWN'))
        print(d.get('url', ''))
        sys.exit(0)
# If not found by SHA, use the most recent deployment
if deployments:
    d = deployments[0]
    print(d.get('state', 'UNKNOWN'))
    print(d.get('url', ''))
" 2>/dev/null | head -2)"

        local deploy_state
        deploy_state="$(echo "$state" | head -1)"
        deploy_url="$(echo "$state" | tail -1)"
        build_log_url="https://vercel.com/dashboard"
        [ -n "$deploy_url" ] && build_log_url="https://${deploy_url}/_logs"

        case "$deploy_state" in
            READY)
                echo -e "${TEAL}${BOLD}  ✓ DEPLOY OK — https://${deploy_url}${RESET}"
                return 0
                ;;
            ERROR)
                echo -e "${RED}${BOLD}  ✗ DEPLOY FAILED — build log: ${build_log_url}${RESET}"
                # Post blocker to agent_messages
                "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'sub_tag': '$CC_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'blocker',
    'subject': 'DEPLOY FAILED — ${REPO_NAME} commit ${commit_sha:0:8}',
    'body': 'Vercel deployment reached ERROR state.\nCommit: ${commit_sha}\nBuild log: ${build_log_url}\nAction required: read build log, fix, push again.',
    'requires_response': True,
}).execute()
" 2>/dev/null || true
                return 1
                ;;
            BUILDING|INITIALIZING|QUEUED)
                echo -e "${DIM}  poll ${poll_count}: state=${deploy_state}, waiting 20s...${RESET}"
                sleep 20
                ;;
            *)
                echo -e "${DIM}  poll ${poll_count}: state=${deploy_state:-unknown}, waiting 15s...${RESET}"
                sleep 15
                ;;
        esac
    done

    # Timeout — flag as blocker
    echo -e "${AMBER}${BOLD}  ⚠ DEPLOY TIMEOUT — still not READY after 5 minutes${RESET}"
    echo -e "${AMBER}  Check: https://vercel.com/dashboard${RESET}"
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'sub_tag': '$CC_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'blocker',
    'subject': 'DEPLOY TIMEOUT — ${REPO_NAME} commit ${commit_sha:0:8}',
    'body': 'Vercel deployment did not reach READY within 5 minutes.\nCommit: ${commit_sha}\nCheck Vercel dashboard for build status.',
    'requires_response': True,
}).execute()
" 2>/dev/null || true
    return 1
}

# ── 6. EXIT trap ──────────────────────────────────────────────────────────────

_handle_exit() {
    local exit_code=$?

    # Kill background loops immediately
    [ -n "$HEARTBEAT_PID" ] && kill "$HEARTBEAT_PID" 2>/dev/null || true

    # Remove the interactive-session marker so Phase 3 sweeps stop yielding.
    # If the marker stayed, a crashed session that didn't restart would block
    # all background CLI spawns indefinitely (yield mechanism keeps polling
    # until mtime > threshold; marker without active heartbeat refresh would
    # eventually go stale on its own, but explicit removal is cleaner).
    rm -f "$HOME/.wingmen/cc_active" 2>/dev/null || true

    local session_end_epoch
    session_end_epoch="$(date -u +%s)"
    local duration_seconds=$(( session_end_epoch - SESSION_START_EPOCH ))
    local session_end_ts
    session_end_ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local outcome
    outcome="$([ "$exit_code" -eq 0 ] && echo 'completed' || echo 'interrupted')"

    echo ""
    echo -e "${DIM}Session ended: ${outcome} | exit_code=${exit_code} | duration=${duration_seconds}s${RESET}"

    # Auto-push any unpushed commits before closing out, then verify Vercel deploy
    local ahead
    ahead="$(git -C "$CALLER_DIR" log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${ahead:-0}" -gt 0 ]; then
        echo -e "${AMBER}▶ Pushing ${ahead} unpushed commit(s) before exit...${RESET}"
        if git -C "$CALLER_DIR" push origin main 2>&1; then
            _verify_vercel_deploy "$CALLER_DIR"
            # ARCH-024: write repo_snapshot after successful push.
            # CAI-RESP-093 immediate fix: stderr was masked by `2>/dev/null` and
            # `|| true` swallowed all failures silently. We keep the trap-completion
            # guarantee (writer failures must NOT abort the launcher exit-trap) but
            # surface stderr to launchd's log capture and explicitly log the outcome
            # so launcher logs answer "did the snapshot writer fire and succeed?"
            if "$VENV_PY" -m scripts.write_repo_snapshot \
                    --repo "$REPO_NAME" --dir "$CALLER_DIR"; then
                echo -e "${DIM}  ✓ repo_snapshot written for ${REPO_NAME}${RESET}"
            else
                echo -e "${RED}  ⚠ repo_snapshot write FAILED for ${REPO_NAME} (non-fatal, continuing)${RESET}" >&2
            fi
        else
            echo -e "${RED}⚠ git push failed — commits remain local${RESET}"
        fi
    fi

    # Write cc_work_sessions row
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('cc_work_sessions').insert({
    'repo_name': '$REPO_NAME',
    'triggered_by': 'launch_dangerous_cc',
    'narrative': 'Dangerous-mode session ended at $session_end_ts',
    'outcome': '$outcome',
    'duration_seconds': $duration_seconds,
}).execute()
" 2>/dev/null || true

    # ARCH-035: flip agent_status to offline for THIS sub-tag ($CC_AGENT_ID,
    # aliased as $AGENT_ID above). psycopg direct is mandatory for GUC
    # semantics (SET LOCAL + UPDATE must be one transaction for the trigger
    # to see the GUC match). Survives clean exit + SIGTERM (trap fires).
    # Does NOT survive kill -9 — stale_agents view catches that via 15-min
    # heartbeat threshold.
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.exit(0)

dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)

try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('$AGENT_ID',))
            cur.execute(
                '''
                UPDATE agent_status
                   SET status = 'offline',
                       current_task = NULL,
                       last_heartbeat = now(),
                       updated_at = now()
                 WHERE agent_id = %s
                ''',
                ('$AGENT_ID',)
            )
        conn.commit()
except Exception as e:
    sys.stderr.write(f'exit: agent_status offline UPSERT failed: {e}\n')
" 2>&1 | grep -E '^exit:' || true

    # Post session-end agent_message
    local subject
    subject="Session ${outcome}: ${REPO_NAME} (${duration_seconds}s) — $(date -u '+%Y-%m-%d %H:%M UTC')"
    if [ "$outcome" = "interrupted" ]; then
        subject="[INTERRUPTED] ${subject}"
    fi

    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
# agent_messages.from_agent uses BASE (FK-enforced). Sub-tag goes in sub_tag column.
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'sub_tag': '$CC_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'update',
    'subject': '$subject',
    'body': 'Session ended. Outcome: $outcome. Duration: ${duration_seconds}s. Repo: $REPO_NAME. Exit code: $exit_code.',
    'requires_response': False,
}).execute()
# Flip BASE family status to idle (legacy agents table). Sub-tag agent_status
# was flipped to offline in the psycopg block above.
sb.table('agents').update({'status': 'idle', 'current_task': None}).eq('id', '$BASE_AGENT_ID').execute()
" 2>/dev/null || true
}

trap '_handle_exit' EXIT

# ── 6. Launch claude (Step 3: Opus 4.7 default + MODEL env override) ─────────
#
# Delta-v2 non-load-bearing #3 — model precedence order (LAST argv --model
# wins, because `claude` uses last-wins flag parsing):
#   1. CLAUDE_PASSTHROUGH --model foo   — highest priority (operator escape
#                                         hatch via `-- --model foo` on the
#                                         launcher command line).
#   2. MODEL env var                    — resolves RESOLVED_MODEL, applied as
#                                         the first --model flag on argv.
#   3. hardcoded default claude-opus-4-8 — falls through when MODEL unset.
# Sequencing: resolve RESOLVED_MODEL from (MODEL env || default) → append
# `--model $RESOLVED_MODEL` to the claude call → append "${CLAUDE_PASSTHROUGH[@]}"
# AFTER it, so a passthrough --model overrides by coming later on argv.

RESOLVED_MODEL="${MODEL:-claude-opus-4-8}"
echo -e "${BOLD}${TEAL}▶ Resolved model: ${RESOLVED_MODEL}${RESET}"
if [ "$RESOLVED_MODEL" != "claude-opus-4-8" ]; then
    echo -e "${AMBER}  (override via MODEL env var — default is claude-opus-4-8)${RESET}"
fi

# Also stamp resolved model into current_task so CAI can observe model drift
# across sessions via agent_status.current_task sampling.
"$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.exit(0)
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)
try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('$CC_AGENT_ID',))
            cur.execute(
                \"UPDATE agent_status SET current_task = %s, updated_at=now() WHERE agent_id = %s\",
                ('session-launch model=$RESOLVED_MODEL repo=$REPO_NAME', '$CC_AGENT_ID'),
            )
        conn.commit()
except Exception:
    pass
" 2>/dev/null || true

echo -e "${BOLD}${TEAL}▶ Launching claude --dangerously-skip-permissions in: ${CALLER_DIR}${RESET}"
echo -e "${DIM}  Heartbeat loop: PID ${HEARTBEAT_PID} (5-min intervals)${RESET}"
echo ""

# Restore caller's directory for the actual claude session
cd "$CALLER_DIR"

# CLAUDE_PASSTHROUGH was populated by the single-pass arg parser at the top of
# this script; use it directly.

if [ -n "$LAUNCH_CONTEXT" ] && [ ${#CLAUDE_PASSTHROUGH[@]} -eq 0 ]; then
    # BUG-011 fix: write context to temp file. The SessionStart hook in
    # ~/.claude/settings.local.json reads and deletes the file, injecting it
    # as a system-reminder on startup. Piping via stdin (or -p) makes claude
    # exit after processing the input — the session would not be interactive.
    echo "$LAUNCH_CONTEXT" > /tmp/cc_launch_ctx.txt
    echo -e "${TEAL}  Context staged → /tmp/cc_launch_ctx.txt (SessionStart hook will inject)${RESET}"
fi

# --model $RESOLVED_MODEL first; CLAUDE_PASSTHROUGH appended AFTER so an
# operator `-- --model X` wins via last-wins flag parsing.
#
# Two launch modes:
#   1. Interactive (default): claude opens an interactive REPL. Context
#      arrives via SessionStart hook as system-reminder.
#   2. Scheduled (--scheduled-prompt set): claude runs -p with the prompt
#      file contents as the initial user message. Non-interactive — claude
#      processes the prompt with tools + exits. Bounded by --max-turns
#      (typically passed via CLAUDE_PASSTHROUGH from scheduled_cc_sweep.sh)
#      and the launchd plist ExitTimeOut. Section E Phase 3.
if [ -n "$SCHEDULED_PROMPT_FILE" ]; then
    PROMPT_PATH="$ORCH_DIR/$SCHEDULED_PROMPT_FILE"
    if [ ! -f "$PROMPT_PATH" ]; then
        echo -e "${RED}ERROR: --scheduled-prompt file not found: $PROMPT_PATH${RESET}" >&2
        exit 1
    fi
    echo -e "${TEAL}  Scheduled-sweep mode: reading prompt from $SCHEDULED_PROMPT_FILE${RESET}"
    PROMPT_TEXT="$(cat "$PROMPT_PATH")"
    claude --dangerously-skip-permissions --model "$RESOLVED_MODEL" -p "$PROMPT_TEXT" "${CLAUDE_PASSTHROUGH[@]+"${CLAUDE_PASSTHROUGH[@]}"}"
else
    # Always launch interactively. Context (if staged above) arrives via the
    # SessionStart hook as a system-reminder, not via stdin.
    claude --dangerously-skip-permissions --model "$RESOLVED_MODEL" "${CLAUDE_PASSTHROUGH[@]+"${CLAUDE_PASSTHROUGH[@]}"}"
fi
