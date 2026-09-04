#!/usr/bin/env bash
# cc_storefront_audit_drain_sweep.sh — CAI-1348 audit-board drainer.
#
# Per-tick wrapper (launchctl, ~12h) that drains cc-storefront's OWN stale
# decision_audits backlog. Two-stage, mirroring scheduled_cc_sweep.sh:
#
#   1. Pre-filter via Python+psycopg: count OPEN decision_audits assigned to
#      cc-storefront (completed_at IS NULL AND resolved_at IS NULL). If zero,
#      heartbeat + exit (cheap empty-tick fast-path).
#
#   2. If non-empty: spawn a bounded cc-storefront CC session IN A DEDICATED
#      WORKTREE (never the live storefront lane dir — CAI-RESP-1361, avoids two
#      `claude` in one dir) via launch_dangerous_cc.sh --scheduled-prompt
#      pointing at skills/audit-drain-sweep-prompt.md. The session fast-triages
#      (CAI-RESP-1348 §1) or full-re-audits up to AUDIT_DRAIN_BATCH items,
#      writes verdict+completed_at, exits within the plist ExitTimeOut.
#
# Identity: the launcher resolves the family from the CALLER's pwd (git-toplevel
# basename -> family map). The dedicated worktree basename
# 'ihsanos-storefront.wt-cai1348' strips to 'ihsanos-storefront' -> cc-storefront.
# So this wrapper MUST `cd` into the worktree before exec'ing the launcher.
#
# This wrapper never touches decision_audits itself — it only orchestrates
# spawn-vs-skip. All audit judgment + the completion write happen inside the
# spawned session, per the prompt's guardrails (no fabricated verdicts).
set -euo pipefail

ORCH_DIR="/Users/sheikhmusa/wingmen/orchestrator"
VENV_PY="${ORCH_DIR}/.venv/bin/python"
SWEEP_WORKTREE="/Users/sheikhmusa/wingmen/projects/ihsanos-storefront.wt-cai1348"
AUDITOR_AGENT="cc-storefront"
PROMPT_FILE="skills/audit-drain-sweep-prompt.md"
# Bounded per-tick batch. Non-urgent hygiene: steady drain over a few ticks
# beats one unbounded race. Overridable via env for a wider manual sweep.
export AUDIT_DRAIN_BATCH="${AUDIT_DRAIN_BATCH:-3}"
# FULL-tier auditor doctrine: governance/PII/residency verdicts run at opus-4-8,
# NOT the fleet_model cost-flip tier (reference_full_tier_audit_requires_opus_4_8
# / CAI-RESP-1170). Pin it here so a sonnet flip can never silently downgrade a
# governance re-audit verdict. Overridable only by an explicit caller MODEL.
export MODEL="${MODEL:-claude-opus-4-8}"

cd "${ORCH_DIR}"
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ ! -x "${VENV_PY}" ]; then
    echo "audit_drain_sweep: venv python missing at ${VENV_PY}" >&2
    exit 78
fi
if [ ! -d "${SWEEP_WORKTREE}/.git" ] && ! git -C "${SWEEP_WORKTREE}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "audit_drain_sweep: dedicated worktree missing at ${SWEEP_WORKTREE} — create it before enabling this sweep" >&2
    exit 78
fi

export SWEEP_AUDITOR_AGENT="${AUDITOR_AGENT}"

# Pre-filter: count OPEN decision_audits for this auditor. "N" or "ERR".
open_count=$("${VENV_PY}" - <<'PYEOF' 2>/dev/null || echo "ERR"
import os, sys
from dotenv import load_dotenv
load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
import psycopg
agent = os.environ.get("SWEEP_AUDITOR_AGENT")
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not (agent and dsn):
    sys.exit(1)
with psycopg.connect(dsn, autocommit=True, connect_timeout=8) as c:
    with c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM decision_audits "
            "WHERE auditor_agent=%s AND completed_at IS NULL AND resolved_at IS NULL",
            (agent,)
        )
        print(cur.fetchone()[0])
PYEOF
)

# Decode; on ERR/malformed default to spawn (fail-open — a transient DB glitch
# must not silently swallow real backlog).
if [[ "${open_count}" =~ ^[0-9]+$ ]]; then
    :
else
    open_count="?"
fi

if [ "${open_count}" = "0" ]; then
    echo "[${ts}] ${AUDITOR_AGENT}: empty tick (open_audits=0) — heartbeat + skip CC spawn"
    "${VENV_PY}" - <<'PYEOF' >/dev/null 2>&1 || true
import os
from dotenv import load_dotenv
load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
import psycopg
agent = os.environ.get("SWEEP_AUDITOR_AGENT")
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if agent and dsn:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=8) as c:
        with c.cursor() as cur:
            cur.execute("UPDATE agents SET last_heartbeat=now() WHERE id=%s", (agent,))
PYEOF
    exit 0
fi

echo "[${ts}] ${AUDITOR_AGENT}: spawning bounded audit-drain CC (open_audits=${open_count}, batch=${AUDIT_DRAIN_BATCH}) in ${SWEEP_WORKTREE}"

# cd into the DEDICATED worktree so identity resolves to cc-storefront and the
# session runs off the live lane dir. The launcher reads --scheduled-prompt
# relative to its own ORCH_DIR, so the prompt path stays orchestrator-relative.
cd "${SWEEP_WORKTREE}"
exec "${ORCH_DIR}/scripts/launch_dangerous_cc.sh" \
    --scheduled-prompt "${PROMPT_FILE}" \
    -- \
    --max-turns 30
