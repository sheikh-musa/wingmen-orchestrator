#!/usr/bin/env bash
# local_ci_runner.sh — the `tests` check (op#35690, docs/loop-pilot-design-v1.md).
#
# hifz's GitHub Actions budget is exhausted, so this mirrors .github/workflows/ci.yml
# step-for-step and posts the `tests` commit status from a local runner instead.
# Does NOT run the per-task acceptance tests (that's the reviewer role's job,
# feeding into the separate reviewer-verdict check) — only hifz's own
# pre-existing lint/typecheck/build/smoke gate, same steps ci.yml runs today.
#
# Usage:
#   scripts/loop/local_ci_runner.sh --repo-dir <path> --sha <sha> \
#       [--post-status --owner <owner> --repo <repo> --orch-dir <path>]
set -euo pipefail

POST_STATUS=0
REPO_DIR=""
SHA=""
OWNER=""
REPO=""
ORCH_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir) REPO_DIR="$2"; shift 2 ;;
    --sha) SHA="$2"; shift 2 ;;
    --owner) OWNER="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --orch-dir) ORCH_DIR="$2"; shift 2 ;;
    --post-status) POST_STATUS=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$REPO_DIR" || -z "$SHA" ]]; then
  echo "usage: local_ci_runner.sh --repo-dir <path> --sha <sha> [--post-status --owner <o> --repo <r> --orch-dir <path>]" >&2
  exit 2
fi
if [[ "$POST_STATUS" == "1" ]] && { [[ -z "$OWNER" ]] || [[ -z "$REPO" ]] || [[ -z "$ORCH_DIR" ]]; }; then
  echo "--post-status requires --owner, --repo, and --orch-dir" >&2
  exit 2
fi

cd "$REPO_DIR"

STEPS_LOG="$(mktemp)"
trap 'rm -f "$STEPS_LOG"' EXIT

FAIL_STEP=""

run_step() {
  local name="$1"; shift
  echo "== $name ==" >>"$STEPS_LOG"
  if ! "$@" >>"$STEPS_LOG" 2>&1; then
    FAIL_STEP="$name"
    return 1
  fi
  return 0
}

RESULT=success

# Mirrors .github/workflows/ci.yml step order exactly (npm ci, bump version,
# playwright install, lint, typecheck, build, smoke:audit) — not reordered
# for logical neatness, so this check means what ci.yml means.
if ! run_step "npm ci" npm ci; then RESULT=failure
elif ! run_step "update-version" npm run update-version; then RESULT=failure
elif { ! npx playwright --version >/dev/null 2>&1 || [[ ! -d "$HOME/Library/Caches/ms-playwright" ]]; } \
     && ! run_step "playwright install" npx playwright install --with-deps chromium; then
  RESULT=failure
elif ! run_step "lint" npm run lint; then RESULT=failure
elif ! run_step "typecheck" npm run typecheck; then RESULT=failure
elif ! run_step "build" npm run build; then RESULT=failure
elif ! run_step "smoke:audit" npm run smoke:audit; then RESULT=failure
fi

if [[ "$RESULT" == "failure" ]]; then
  DESCRIPTION="failed at: $FAIL_STEP"
else
  DESCRIPTION="all CI steps passed (npm ci, update-version, playwright install, lint, typecheck, build, smoke:audit)"
fi

echo "RESULT=$RESULT"
echo "---- last 60 lines of CI output ----"
tail -n 60 "$STEPS_LOG"

if [[ "$POST_STATUS" == "1" ]]; then
  "$ORCH_DIR/scripts/loop/gh_status.py" --owner "$OWNER" --repo "$REPO" \
      --sha "$SHA" --state "$RESULT" --context tests \
      --description "$DESCRIPTION"
fi

[[ "$RESULT" == "success" ]]
