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
#   MODEL        — claude model override (default: claude-opus-4-7)
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
REMINDER_PID=""

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
    # Two heartbeats on a 5-minute cadence:
    #   1. agents.last_heartbeat (base id, legacy agents table)
    #   2. agent_status.last_heartbeat (sub-tag, ARCH-035 with GUC)
    # Both are best-effort; if the worker misses a beat, stale_agents view
    # (15-min threshold) catches it.
    while true; do
        sleep 300
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
" 2>/dev/null || true
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
            # ARCH-024: write repo_snapshot after successful push (best-effort)
            "$VENV_PY" -m scripts.write_repo_snapshot \
                --repo "$REPO_NAME" --dir "$CALLER_DIR" 2>/dev/null || true
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

    # ARCH-035: flip agent_status to offline (psycopg direct for GUC).
    # Survives clean exit + SIGTERM (trap fires). Does NOT survive kill -9 —
    # stale_agents view catches that via 15-min heartbeat threshold.
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

# CLAUDE_PASSTHROUGH was populated by the single-pass arg parser at the top of
# this script. Use it directly (CLAUDE_ARGS alias for readability).
CLAUDE_ARGS=("${CLAUDE_PASSTHROUGH[@]+"${CLAUDE_PASSTHROUGH[@]}"}")

if [ -n "$LAUNCH_CONTEXT" ] && [ ${#CLAUDE_ARGS[@]} -eq 0 ]; then
    # BUG-011 fix: write context to temp file. The SessionStart hook in
    # ~/.claude/settings.local.json reads and deletes the file, injecting it
    # as a system-reminder on startup. Piping via stdin (or -p) makes claude
    # exit after processing the input — the session would not be interactive.
    echo "$LAUNCH_CONTEXT" > /tmp/cc_launch_ctx.txt
    echo -e "${TEAL}  Context staged → /tmp/cc_launch_ctx.txt (SessionStart hook will inject)${RESET}"
fi

# Always launch interactively. Context (if staged above) arrives via the
# SessionStart hook as a system-reminder, not via stdin.
claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}"
