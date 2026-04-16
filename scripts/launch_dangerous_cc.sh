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
#   CC_AGENT_ID=cc-web ./scripts/launch_dangerous_cc.sh
#   ./scripts/launch_dangerous_cc.sh -- --resume <session-id>
#
# Environment:
#   CC_AGENT_ID  — agent id to boot as (default: cc-ihsanos)
#   CC_REPO      — repo name for cc_work_sessions row (default: autodetect from git)

set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
CALLER_DIR="$(pwd)"

AGENT_ID="${CC_AGENT_ID:-cc-ihsanos}"

# Auto-detect repo name from caller's git directory, or use fallback
REPO_NAME="${CC_REPO:-}"
if [ -z "$REPO_NAME" ]; then
    REPO_NAME="$(git -C "$CALLER_DIR" rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || echo "unknown")"
fi

SESSION_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SESSION_START_EPOCH="$(date -u +%s)"
HEARTBEAT_PID=""
REMINDER_PID=""

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
echo -e "${BOLD}${TEAL}║   WINGMEN AGENT BOOT — ARCH-022 Layer 2                             ║${RESET}"
echo -e "${BOLD}${TEAL}╚══════════════════════════════════════════════════════════════════════╝${RESET}"
echo -e "${DIM}Agent: ${AGENT_ID}  |  Repo: ${REPO_NAME}  |  Started: ${SESSION_START}${RESET}"
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

echo -e "${BOLD}▶ Building session context...${RESET}"
# Stdout = context block (captured). Stderr = diagnostics (shown on terminal).
LAUNCH_CONTEXT="$("$VENV_PY" -m scripts.build_launch_context --agent "$AGENT_ID")" || {
    echo -e "${AMBER}⚠ build_launch_context failed. Continuing without injected context.${RESET}"
    LAUNCH_CONTEXT=""
}

if [ -n "$LAUNCH_CONTEXT" ]; then
    echo -e "${TEAL}  Context assembled: $(echo "$LAUNCH_CONTEXT" | wc -l | tr -d ' ') lines, $(echo -n "$LAUNCH_CONTEXT" | wc -c | tr -d ' ') chars${RESET}"
else
    echo -e "${AMBER}  No context to inject.${RESET}"
fi
echo ""

# ── 3. Start background heartbeat loop ────────────────────────────────────────

_heartbeat_loop() {
    while true; do
        sleep 300  # every 5 minutes
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
from datetime import datetime, timezone
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agents').update({'last_heartbeat': datetime.now(timezone.utc).isoformat()}).eq('id', '$AGENT_ID').execute()
" 2>/dev/null || true
    done
}

_heartbeat_loop &
HEARTBEAT_PID=$!

# ── 4. Start check-in reminder loop ───────────────────────────────────────────

_reminder_loop() {
    while true; do
        sleep 1500  # every 25 minutes
        echo ""
        echo -e "${AMBER}${BOLD}╔══════════════════════════════════════════════════════════════════════╗${RESET}"
        echo -e "${AMBER}${BOLD}║  CHECK-IN DUE (ARCH-022) — post agent_message to cai:              ║${RESET}"
        echo -e "${AMBER}${BOLD}║    message_type=update, what shipped, what's in progress, blockers  ║${RESET}"
        echo -e "${AMBER}${BOLD}╚══════════════════════════════════════════════════════════════════════╝${RESET}"
        # Check for unpushed commits in caller's repo
        local ahead
        ahead="$(git -C "$CALLER_DIR" log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' ')"
        if [ "${ahead:-0}" -gt 0 ]; then
            echo -e "${RED}${BOLD}  ⚠ UNPUSHED COMMITS ($ahead ahead of origin) — run: git push origin main${RESET}"
        fi
        echo ""
    done
}

_reminder_loop &
REMINDER_PID=$!

# ── 5. Vercel deployment verification ─────────────────────────────────────────
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
    'from_agent': '$AGENT_ID',
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
    'from_agent': '$AGENT_ID',
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
    [ -n "$REMINDER_PID" ]  && kill "$REMINDER_PID"  2>/dev/null || true

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
sb.table('agent_messages').insert({
    'from_agent': '$AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'update',
    'subject': '$subject',
    'body': 'Session ended. Outcome: $outcome. Duration: ${duration_seconds}s. Repo: $REPO_NAME. Exit code: $exit_code.',
    'requires_response': False,
}).execute()
# Flip agent status to idle
sb.table('agents').update({'status': 'idle', 'current_task': None}).eq('id', '$AGENT_ID').execute()
" 2>/dev/null || true
}

trap '_handle_exit' EXIT

# ── 6. Launch claude ──────────────────────────────────────────────────────────

echo -e "${BOLD}${TEAL}▶ Launching claude --dangerously-skip-permissions in: ${CALLER_DIR}${RESET}"
echo -e "${DIM}  Heartbeat loop: PID ${HEARTBEAT_PID} (5-min intervals)${RESET}"
echo -e "${DIM}  Check-in loop:  PID ${REMINDER_PID} (25-min reminders)${RESET}"
echo ""

# Restore caller's directory for the actual claude session
cd "$CALLER_DIR"

# Pass any extra args after -- to claude
CLAUDE_ARGS=()
PASS_THROUGH=false
for arg in "$@"; do
    if [ "$arg" = "--" ]; then
        PASS_THROUGH=true
        continue
    fi
    if $PASS_THROUGH; then
        CLAUDE_ARGS+=("$arg")
    fi
done

if [ -n "$LAUNCH_CONTEXT" ] && [ ${#CLAUDE_ARGS[@]} -eq 0 ]; then
    # Inject context as initial prompt. Claude processes it then exits (non-interactive).
    # For an interactive session, remove -p and paste the context manually.
    claude --dangerously-skip-permissions -p "$LAUNCH_CONTEXT"
else
    # Resume / passthrough mode — context already in session, no -p injection.
    claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}"
fi
