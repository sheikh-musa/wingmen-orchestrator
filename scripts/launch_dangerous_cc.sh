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

# ── 2. Run agent_boot ─────────────────────────────────────────────────────────

echo -e "${BOLD}▶ Running agent boot...${RESET}"
"$VENV_PY" -m scripts.agent_boot --agent "$AGENT_ID" 2>/dev/null || {
    echo -e "${AMBER}⚠ agent_boot failed (network issue?). Continuing without DB context.${RESET}"
}
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

# ── 5. EXIT trap ──────────────────────────────────────────────────────────────

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

    # Auto-push any unpushed commits before closing out
    local ahead
    ahead="$(git -C "$CALLER_DIR" log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${ahead:-0}" -gt 0 ]; then
        echo -e "${AMBER}▶ Pushing ${ahead} unpushed commit(s) before exit...${RESET}"
        git -C "$CALLER_DIR" push origin main 2>&1 || \
            echo -e "${RED}⚠ git push failed — commits remain local${RESET}"
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

claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}"
