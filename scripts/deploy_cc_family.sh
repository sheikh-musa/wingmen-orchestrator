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

echo "deploy_cc_family.sh: family-id=$FAMILY_ID dry-run=$DRY_RUN"
echo "  (skeleton — preconditions not yet implemented)"
