#!/usr/bin/env bash
# scheduled_cc_sweep.sh — CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 3.
#
# Per-tick wrapper invoked by launchctl per-family plist. Two-stage:
#
#   1. Pre-filter via Python+psycopg (orchestrator has no psql binary; uses
#      the same .venv supabase-py / psycopg stack as wingmen_orch). Counts
#      unread + active P1/P2 SLA violations (post-CADENCE-001-filing-date)
#      for this family. If both zero, heartbeat + exit.
#
#   2. If non-empty: spawn launch_dangerous_cc.sh with --scheduled-prompt
#      pointing at skills/scheduled-sweep-prompt.md. The CC session boots,
#      applies Section A semantics + Section D guardrails to its own inbox,
#      exits within the launchd ExitTimeOut.
#
# Section D guardrails enforced upstream by the prompt (CC must NOT set
# read_at without in-session read; MUST NOT set responded_at from sweep).
# This wrapper only orchestrates spawn-vs-skip — it never touches
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
VENV_PY="${ORCH_DIR}/.venv/bin/python"
cd "${ORCH_DIR}"

if [ ! -x "${VENV_PY}" ]; then
    echo "scheduled_cc_sweep: venv python missing at ${VENV_PY}" >&2
    exit 78
fi

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Heredoc subshells inherit env, not bash locals — export before invoking.
export SCHEDULED_FAMILY="${FAMILY}"

# Pre-filter via Python+psycopg. Returns "unread,sla" or "ERR" on exception.
# Empty-tick fast-path keeps cost low — typical run is ~200ms (interpreter
# + connection establishment). Connect-on-error: fall through to spawn-CC
# so transient DB glitches don't silently swallow real work.
counts=$("${VENV_PY}" - <<'PYEOF' 2>/dev/null || echo "ERR"
import os, sys
from dotenv import load_dotenv
load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
import psycopg

# CADENCE_001_FILING_DATE — pinned to nervous_system/agent_watchdog.py
# so any future amendment must update both surfaces.
CUTOFF = "2026-04-28T22:30:00+00:00"
family = os.environ.get("SCHEDULED_FAMILY")
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not (family and dsn):
    sys.exit(1)
with psycopg.connect(dsn, autocommit=True, connect_timeout=8) as c:
    with c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM agent_messages WHERE to_agent=%s AND read_at IS NULL",
            (family,)
        )
        unread = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM inbox_sla_violations "
            "WHERE agent=%s AND priority IN ('P1','P2') AND created_at >= %s",
            (family, CUTOFF)
        )
        sla = cur.fetchone()[0]
print(f"{unread},{sla}")
PYEOF
)

# Decode counts; on ERR or malformed, default to spawn-CC (fail-open).
if [[ "${counts}" =~ ^([0-9]+),([0-9]+)$ ]]; then
    unread="${BASH_REMATCH[1]}"
    sla="${BASH_REMATCH[2]}"
else
    unread="?"
    sla="?"
fi

# Spawn decision: ONLY based on unread count. SLA count is for telemetry,
# not spawn — many SLA violations are 'unresponded' on already-read messages
# that the sweep CC has no legal way to act on per Section D (responded_at
# is reserved for substantive dialogue, not sweep). Spawning on those just
# burns Opus 4.7 tokens to confirm "can't help, exiting." Empirical
# observation 2026-04-30: 50 fires × all-spawn × ~$0.10-0.20 each = real
# money before this fix.
if [ "${unread}" = "0" ]; then
    echo "[${ts}] ${FAMILY}: empty tick (unread=${unread} sla=${sla} — sla-only, sweep can't act per Section D) — heartbeat + skip CC spawn"
    # Heartbeat-only path. Targets the family-base `agents.last_heartbeat`
    # (single row, e.g. id='cc-orchestrator') — that surface is what
    # agent_watchdog._check_heartbeat_staleness monitors.
    #
    # Per cc-orchestrator-5 sweep self-audit (2026-04-29 20:16Z): the prior
    # version targeted agent_status sub-tag rows with WHERE status='active',
    # but the only valid agent_status statuses are 'working'+'offline' so
    # the UPDATE was silently no-op. Sub-tag liveness is the launcher's job
    # via auto_agent_id; the wrapper's job is family-level liveness.
    #
    # Safe-fail: heartbeat is informational; main wingmen_orch loop also
    # writes heartbeat for the family base, so a failure here doesn't hide
    # process liveness.
    "${VENV_PY}" - <<'PYEOF' >/dev/null 2>&1 || true
import os
from dotenv import load_dotenv
load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
import psycopg
family = os.environ.get("SCHEDULED_FAMILY")
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if family and dsn:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=8) as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE agents SET last_heartbeat=now() WHERE id=%s",
                (family,)
            )
PYEOF
    exit 0
fi

echo "[${ts}] ${FAMILY}: spawning scheduled CC (unread=${unread} sla=${sla})"

# Spawn the bounded CC session. The launcher's --scheduled-prompt flag
# routes claude into non-interactive mode (claude -p) pointed at
# skills/scheduled-sweep-prompt.md. --max-turns 20 + plist ExitTimeOut=600
# enforce the bounded-session contract.
exec "${ORCH_DIR}/scripts/launch_dangerous_cc.sh" \
    --repo orchestrator \
    --scheduled-prompt skills/scheduled-sweep-prompt.md \
    -- \
    --max-turns 20
