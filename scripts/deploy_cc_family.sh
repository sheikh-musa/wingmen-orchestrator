#!/usr/bin/env bash
# deploy_cc_family.sh — TASK-045 preflight for CC-family spin-up.
#
# Usage: deploy_cc_family.sh [--dry-run] <family-id>
#
# Validates preconditions for bringing a dark CC family (cc-scholar, cc-cosem,
# cc-web) online via scripts/launch_dangerous_cc.sh. On success, prints the
# exact interactive launcher invocation for the operator to paste into a new
# terminal. On failure, exits non-zero with a specific code (see runbook).
#
# Parent: strategic_decisions.decision_ref='TASK-045'
# Runbook: docs/runbooks/deploy-cc-family.md

set -euo pipefail

usage() {
  cat <<EOF >&2
usage: deploy_cc_family.sh [--dry-run] <family-id>

  <family-id>    One of: cc-scholar, cc-cosem, cc-web, cc-ihsanos
  --dry-run      Validate preconditions and print the launcher invocation,
                 but do not actually instruct the operator to run anything.

Exit codes:
  0  All preconditions pass
  1  Usage error (wrong args)
  2  Unknown family-id
  3  agents row missing or repo_scope empty
  4  Repo clone missing at ~/wingmen/projects/<repo>
  5  .env missing or incomplete
  6  launch_dangerous_cc.sh missing or non-executable
  7  Active sibling already heartbeating (stale session)

See docs/runbooks/deploy-cc-family.md for the full flow.
EOF
  exit 1
}

DRY_RUN=0
FAMILY_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    cc-*) FAMILY_ID="$1"; shift ;;
    *) echo "error: unexpected argument: $1" >&2; usage ;;
  esac
done

if [[ -z "$FAMILY_ID" ]]; then
  echo "error: family-id required" >&2
  usage
fi

# ---------------------------------------------------------------------------
# Precondition 1: family-id is a known CC family
# ---------------------------------------------------------------------------
case "$FAMILY_ID" in
  cc-scholar|cc-cosem|cc-web|cc-ihsanos) ;;
  *)
    echo "error: unknown family '$FAMILY_ID'" >&2
    echo "       valid: cc-scholar, cc-cosem, cc-web, cc-ihsanos" >&2
    exit 2
    ;;
esac

# ---------------------------------------------------------------------------
# Precondition 2: repo clones exist at CC_PROJECTS_ROOT/<repo>
# ---------------------------------------------------------------------------
# repo_scope per family (hardcoded mirror of agents.repo_scope — updated when
# CAI-AGENTS-001 changes. Kept in sync manually; DB preflight in the Python
# helper catches drift.)
case "$FAMILY_ID" in
  cc-ihsanos) REPOS=("ihsanos" "wingmen-orchestrator") ;;
  cc-cosem)   REPOS=("cosem-tdu" "cosem-adcda") ;;
  cc-scholar) REPOS=("ai-scholar" "hifz-companion") ;;
  cc-web)     REPOS=("wordpress-sites" "dookana") ;;
esac

PROJECTS_ROOT="${CC_PROJECTS_ROOT:-$HOME/wingmen/projects}"

for repo in "${REPOS[@]}"; do
  if [[ ! -d "$PROJECTS_ROOT/$repo/.git" ]]; then
    echo "error: repo clone missing: $PROJECTS_ROOT/$repo/.git" >&2
    echo "       clone it: git clone git@github.com:<org>/$repo.git $PROJECTS_ROOT/$repo" >&2
    exit 4
  fi
done

# ---------------------------------------------------------------------------
# Precondition 3: .env exists and has required keys
# ---------------------------------------------------------------------------
ENV_FILE="${CC_ENV_FILE:-/Users/sheikhmusa/wingmen/orchestrator/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: .env not found at $ENV_FILE" >&2
  echo "       copy from .env.example and fill in SUPABASE_URL / SUPABASE_SERVICE_KEY / DATABASE_URL" >&2
  exit 5
fi

for key in SUPABASE_URL SUPABASE_SERVICE_KEY DATABASE_URL; do
  if ! grep -qE "^${key}=" "$ENV_FILE"; then
    echo "error: .env missing required key: $key (in $ENV_FILE)" >&2
    exit 5
  fi
done

# ---------------------------------------------------------------------------
# Precondition 4: launch_dangerous_cc.sh exists and is executable
# ---------------------------------------------------------------------------
LAUNCHER="${CC_LAUNCHER:-/Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh}"

if [[ ! -x "$LAUNCHER" ]]; then
  echo "error: wrapper script missing or non-executable: $LAUNCHER" >&2
  echo "       chmod +x $LAUNCHER" >&2
  exit 6
fi

# ---------------------------------------------------------------------------
# Precondition 5: DB preflight (agents row + no active sibling)
# ---------------------------------------------------------------------------
# CC_SKIP_DB=1 bypasses for unit tests that don't need a live DSN.
if [[ "${CC_SKIP_DB:-0}" != "1" ]]; then
  ORCH_DIR="$(cd "$(dirname "$LAUNCHER")/.." && pwd)"
  PY="${ORCH_DIR}/.venv/bin/python"
  if [[ ! -x "$PY" ]]; then
    echo "error: orchestrator venv Python not found at $PY" >&2
    exit 5
  fi
  # Source .env so DATABASE_URL is in the environment for the helper.
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  # Capture stdout+stderr so a Python traceback (uncaught exception) surfaces
  # to the operator instead of being swallowed by set -e / bash pipefail.
  set +e
  DB_OUT="$("$PY" -m scripts.lib.check_family_preflight "$FAMILY_ID" 2>&1)"
  DB_CODE=$?
  set -e
  if [[ $DB_CODE -ne 0 ]]; then
    echo "DB preflight failed (exit $DB_CODE):" >&2
    echo "$DB_OUT" >&2
    exit $DB_CODE
  fi
  echo "  DB preflight: $DB_OUT"
fi

# ---------------------------------------------------------------------------
# All preconditions pass — emit the launcher invocation
# ---------------------------------------------------------------------------
# Pick the FIRST repo in the family's scope as the default worktree.
PRIMARY_REPO="${REPOS[0]}"

LAUNCH_CMD="cd $PROJECTS_ROOT/$PRIMARY_REPO && $LAUNCHER --repo $PRIMARY_REPO"

echo ""
echo "=== Preflight complete for $FAMILY_ID ==="
echo ""
echo "Launcher invocation (paste into a fresh terminal):"
echo ""
echo "  $LAUNCH_CMD"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
  echo "(dry-run — no action taken)"
else
  echo "Open a new terminal window, paste the command above, and run it."
  echo "The launcher will resolve identity, register the session, and start claude."
  echo "See docs/runbooks/deploy-cc-family.md for post-boot verification."
fi

exit 0
