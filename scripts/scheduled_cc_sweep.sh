#!/usr/bin/env bash
# scheduled_cc_sweep.sh — CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 3.
#
# Per-tick wrapper invoked by launchctl per-family plist. Two-stage:
#
#   1. Pre-filter: cheap psql query — does this family have unread messages
#      OR P1/P2 SLA violations right now? If both zero, log heartbeat and
#      exit. Avoids ~80%/family of empty-tick CC spawns.
#
#   2. If non-empty: spawn launch_dangerous_cc.sh with --scheduled-prompt
#      pointing at skills/scheduled-sweep-prompt.md. The CC session boots,
#      applies Section A semantics + Section D guardrails to its own inbox,
#      and exits within the launchd ExitTimeOut.
#
# Section D guardrails enforced upstream by the prompt (CC must NOT set
# read_at without in-session read; MUST NOT set responded_at from sweep).
# This wrapper only orchestrates spawn vs skip — it never touches
# agent_messages itself.
set -euo pipefail

FAMILY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --family)
            FAMILY="$2"
            shift 2
            ;;
        *)
            echo "scheduled_cc_sweep: unknown arg: $1" >&2
            exit 64
            ;;
    esac
done

if [ -z "${FAMILY}" ]; then
    echo "scheduled_cc_sweep: missing --family" >&2
    exit 64
fi

ORCH_DIR="/Users/sheikhmusa/wingmen/orchestrator"
cd "${ORCH_DIR}"

# Source .env to pick up DATABASE_URL — set -a / +a so vars export to subprocs.
set -a
# shellcheck disable=SC1091
source "${ORCH_DIR}/.env"
set +a

if [ -z "${DATABASE_URL:-}" ]; then
    echo "scheduled_cc_sweep: DATABASE_URL missing from .env" >&2
    exit 78
fi

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Pre-filter — count unread + active SLA violations for this family.
# 'continue on error' is intentional: if psql blips, fall through to the
# CC spawn path so we don't silently miss work on transient DB glitches.
unread=$(psql "${DATABASE_URL}" -At -v ON_ERROR_STOP=1 -c "
    SELECT count(*) FROM agent_messages
     WHERE to_agent = '${FAMILY}'
       AND read_at IS NULL
" 2>/dev/null || echo "?")

sla=$(psql "${DATABASE_URL}" -At -v ON_ERROR_STOP=1 -c "
    SELECT count(*) FROM inbox_sla_violations
     WHERE agent = '${FAMILY}'
       AND priority IN ('P1','P2')
" 2>/dev/null || echo "?")

if [ "${unread}" = "0" ] && [ "${sla}" = "0" ]; then
    echo "[${ts}] ${FAMILY}: empty tick (unread=0 sla=0) — heartbeat + skip CC spawn"
    # Update last_heartbeat as proof-of-life without spawning a session.
    # Safe to fail silently: heartbeat is informational; main wingmen_orch
    # loop also writes heartbeat for the orchestrator's own sub-tag.
    psql "${DATABASE_URL}" -c "
        UPDATE agent_status
           SET last_heartbeat = now()
         WHERE agent_id LIKE '${FAMILY}%'
           AND status = 'active'
    " >/dev/null 2>&1 || true
    exit 0
fi

echo "[${ts}] ${FAMILY}: spawning scheduled CC (unread=${unread} sla=${sla})"

# Spawn the bounded CC session. The launcher's --scheduled-prompt flag
# (added separately) routes claude into non-interactive mode pointed at
# skills/scheduled-sweep-prompt.md. --max-turns 20 + plist ExitTimeOut=600
# enforce the bounded-session contract.
exec "${ORCH_DIR}/scripts/launch_dangerous_cc.sh" \
    --repo orchestrator \
    --scheduled-prompt skills/scheduled-sweep-prompt.md \
    -- \
    --max-turns 20
