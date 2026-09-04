#!/usr/bin/env bash
# audit_drain_sweep.sh — GENERIC per-auditor audit-board drainer (CAI-1348 fleet-wide).
#
# Generalizes cc_storefront_audit_drain_sweep.sh across FULL-tier auditors (orch-console
# 37304/37307; CAI-RESP-1348 ordered the fix fleet-wide, naming cc-quality). Parameterized by
# AUDITOR_AGENT; the ONLY per-auditor variation is the IDENTITY/ISOLATION seam:
#   - worktree-cd  (cc-storefront): cd into a dedicated git worktree whose basename resolves the
#                  family (ihsanos-storefront.wt-* -> ihsanos-storefront -> cc-storefront).
#   - CC_BASE_OVERRIDE (cc-quality): its home ~/wingmen/quality is NOT a git repo, so no
#                  family-map worktree exists (verified msg 37300); auto_agent_id --base-override
#                  skips pwd->family resolution and forces the identity (CAI-258 guardrail accepts
#                  cc-quality as a safe auditor family). Runs in a DEDICATED non-git dir that is
#                  NOT the live lane dir (CAI-1361 two-claude-in-one-dir avoidance).
# Everything else is IDENTICAL to the cc-storefront wrapper: same skills/audit-drain-sweep-prompt.md
# (genericized to CC_BASE_AGENT_ID, prose-only edit — behavior-preserving), batch=3, opus-4-8 MODEL
# pin, self-guard pre-filter, empty-tick heartbeat, bounded spawn.
#
# The wrapper NEVER touches decision_audits — it only orchestrates spawn-vs-skip. All audit
# judgment + the completion write happen inside the spawned auditor session, as ITSELF, per the
# prompt's guardrails (no fabricated verdicts). The SRE (cc-fleet-health) builds/schedules this
# MECHANISM; it never authors a close (charter §3b ops-not-governance).
#
# INTERIM NOTE (orch-console 37307): cc-storefront keeps its OWN bespoke wrapper+plist LIVE +
# untouched for now; this generic wrapper serves cc-quality. Once the cc-quality drainer is
# proven (audited + live), a separate GATED change repoints cc-storefront's plist onto THIS
# wrapper (AUDITOR_AGENT=cc-storefront config below) + retires the bespoke one (single source),
# with a cc-storefront re-audit of that migration. Do NOT let the two wrappers become permanent.
set -euo pipefail

ORCH_DIR="/Users/sheikhmusa/wingmen/orchestrator"
VENV_PY="${ORCH_DIR}/.venv/bin/python"
PROMPT_FILE="skills/audit-drain-sweep-prompt.md"

AUDITOR_AGENT="${AUDITOR_AGENT:?AUDITOR_AGENT env required (e.g. cc-quality)}"

# Per-auditor identity/isolation config. RUN_DIR = where the bounded session runs (isolated from
# the live lane). CC_BASE_OVERRIDE non-empty = force identity via --base-override (non-git path).
# REQUIRE_GIT=1 = RUN_DIR must be a git worktree (identity via basename).
case "${AUDITOR_AGENT}" in
    cc-storefront)
        RUN_DIR="/Users/sheikhmusa/wingmen/projects/ihsanos-storefront.wt-cai1348"
        BASE_OVERRIDE=""
        REQUIRE_GIT=1
        ;;
    cc-quality)
        # Dedicated non-git isolation dir — NOT the live lane dir ~/wingmen/quality (CAI-1361).
        RUN_DIR="/Users/sheikhmusa/wingmen/quality.audit-drain"
        BASE_OVERRIDE="cc-quality"
        REQUIRE_GIT=0
        ;;
    *)
        echo "audit_drain_sweep: unknown AUDITOR_AGENT '${AUDITOR_AGENT}' — add a config block" >&2
        exit 78
        ;;
esac

# Bounded per-tick batch + FULL-tier opus-4-8 pin (a sonnet cost-flip must never silently
# downgrade a governance re-audit verdict — CAI-RESP-1170). Both overridable via env.
export AUDIT_DRAIN_BATCH="${AUDIT_DRAIN_BATCH:-3}"
export MODEL="${MODEL:-claude-opus-4-8}"

cd "${ORCH_DIR}"
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ ! -x "${VENV_PY}" ]; then
    echo "audit_drain_sweep: venv python missing at ${VENV_PY}" >&2
    exit 78
fi
if [ ! -d "${RUN_DIR}" ]; then
    echo "audit_drain_sweep: RUN_DIR missing at ${RUN_DIR} — create it before enabling this sweep" >&2
    exit 78
fi
if [ "${REQUIRE_GIT}" = "1" ] && ! git -C "${RUN_DIR}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "audit_drain_sweep: RUN_DIR ${RUN_DIR} is not a git worktree (required for ${AUDITOR_AGENT} identity)" >&2
    exit 78
fi

export SWEEP_AUDITOR_AGENT="${AUDITOR_AGENT}"

# Pre-filter: count OPEN decision_audits for this auditor. "N" or "ERR" -> fail-open (spawn).
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

echo "[${ts}] ${AUDITOR_AGENT}: spawning bounded audit-drain CC (open_audits=${open_count}, batch=${AUDIT_DRAIN_BATCH}) in ${RUN_DIR}${BASE_OVERRIDE:+ [CC_BASE_OVERRIDE=${BASE_OVERRIDE}]}"

# cd into the isolated RUN_DIR (off the live lane dir). For a non-git auditor, force identity via
# CC_BASE_OVERRIDE (auto_agent_id --base-override). The launcher reads --scheduled-prompt relative
# to its own ORCH_DIR, so the prompt path stays orchestrator-relative regardless of cwd.
cd "${RUN_DIR}"
if [ -n "${BASE_OVERRIDE}" ]; then
    export CC_BASE_OVERRIDE="${BASE_OVERRIDE}"
fi
exec "${ORCH_DIR}/scripts/launch_dangerous_cc.sh" \
    --scheduled-prompt "${PROMPT_FILE}" \
    -- \
    --max-turns 30
