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

# Pre-filter via Python+psycopg. Returns "unread,unresponded,sla" or "ERR" on exception.
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
        # CAI-RESP-296: read-but-UNANSWERED requires_response asks must also
        # trigger a spawn. The read_at signal alone dropped them permanently
        # (the exact CAI-RESP-274 bug, never applied to this sweep), so routed
        # asks sat unanswered. The spawned session can substantively reply +
        # close the loop (Section D, fixed) — so unresponded is now actionable.
        # CAI-RESP-296 B1 (backoff/dedup — cost guard): exclude asks the sweep
        # has ALREADY surfaced as `scheduled_sweep_dialogue_pending` (Section D
        # case (b): genuinely can't resolve this session — needs operator / a
        # lane / a build). Their responded_at stays unset (no fabricated
        # closure), so without this they'd re-spawn a full CC every 15-min tick
        # forever with zero progress — the exact cost-sink the old unread-only
        # comment warned about. Mirrors the SLA path's notification_log dedup:
        # a known-blocked ask spawns ONCE to surface+flag it, then stops until
        # its state changes (answered, or a fresh unflagged ask arrives). It
        # stays visible (notification_log + boot_briefing + the boot inbox
        # view) — it just no longer FORCES a spawn. dedup_key shape per the
        # sweep prompt: scheduled_sweep:dialogue_pending:<self>:<msg_id>:<bucket>
        cur.execute(
            "SELECT count(*) FROM agent_messages m "
            "WHERE m.to_agent=%s AND m.requires_response=true "
            "AND m.responded_at IS NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM notification_log n "
            "  WHERE n.source='scheduled_sweep.dialogue_pending' "
            "  AND n.dedup_key LIKE 'scheduled_sweep:dialogue_pending:' "
            "      || m.to_agent || ':' || m.id::text || ':%%'"
            ")",
            (family,)
        )
        unresponded = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM inbox_sla_violations "
            "WHERE agent=%s AND priority IN ('P1','P2') AND created_at >= %s",
            (family, CUTOFF)
        )
        sla = cur.fetchone()[0]
print(f"{unread},{unresponded},{sla}")
PYEOF
)

# Decode counts; on ERR or malformed, default to spawn-CC (fail-open).
if [[ "${counts}" =~ ^([0-9]+),([0-9]+),([0-9]+)$ ]]; then
    unread="${BASH_REMATCH[1]}"
    unresponded="${BASH_REMATCH[2]}"
    sla="${BASH_REMATCH[3]}"
else
    unread="?"
    unresponded="?"
    sla="?"
fi

# Spawn decision (CAI-RESP-296): fire on unread OR unresponded.
# - unread: a fresh message not yet read in any session.
# - unresponded: a requires_response item that's been READ but never ANSWERED.
#   Pre-296 the sweep keyed on unread alone, so read-but-unanswered asks (e.g.
#   072-apply #3042, 071-review #3048, +42 others) dropped out permanently and
#   sat 15 days. Now the spawned session resurfaces + genuinely answers them,
#   closing the loop via responded_at (Section D fixed: real reply closes, no
#   fabrication). SLA count stays telemetry-only (the unresponded signal
#   already covers the actionable subset; SLA is the broader observability view).
if [ "${unread}" = "0" ] && [ "${unresponded}" = "0" ]; then
    echo "[${ts}] ${FAMILY}: empty tick (unread=${unread} unresponded=${unresponded} sla=${sla}) — heartbeat + skip CC spawn"
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

echo "[${ts}] ${FAMILY}: spawning scheduled CC (unread=${unread} unresponded=${unresponded} sla=${sla})"

# Spawn the bounded CC session. The launcher's --scheduled-prompt flag
# routes claude into non-interactive mode (claude -p) pointed at
# skills/scheduled-sweep-prompt.md. --max-turns 20 + plist ExitTimeOut=600
# enforce the bounded-session contract.
exec "${ORCH_DIR}/scripts/launch_dangerous_cc.sh" \
    --repo orchestrator \
    --scheduled-prompt skills/scheduled-sweep-prompt.md \
    -- \
    --max-turns 20
