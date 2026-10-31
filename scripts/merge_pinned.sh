#!/usr/bin/env bash
# merge_pinned.sh — the ONE way orch-console merges a reviewed PR.
#
# WHY: on 2026-10-31 orch-console merged orchestrator#730 "pinned" after checking the
# head sha but NOT the CI conclusion — Lint & Test had FAILED, and trunk went red. A
# promise to "also check CI next time" does not survive a context reset
# (feedback_enforce_process_in_code_not_promises), so the check lives here.
#
# Refuses (exit 2) unless ALL of:
#   - the PR head matches the sha you name (prefix ok, min 7 chars) — a reviewed head;
#   - the PR base is what you expect (default fable/substrate-safe-fixes for this repo);
#   - there is at least one check, and EVERY check's bucket is "pass" or "skipping"
#     (pending, fail, cancel, or no checks at all => refuse).
# Then squash-merges with --match-head-commit, so a push between check and merge
# still fails the merge.
#
# Usage: scripts/merge_pinned.sh <pr> <head-sha-prefix> [--repo owner/name] [--base branch]
set -euo pipefail

pr="${1:?usage: merge_pinned.sh <pr> <head-sha-prefix> [--repo R] [--base B]}"
want="${2:?usage: merge_pinned.sh <pr> <head-sha-prefix> [--repo R] [--base B]}"
shift 2
repo=""; base=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --base) base="$2"; shift 2 ;;
    *) echo "merge_pinned: unknown arg $1" >&2; exit 2 ;;
  esac
done
[ ${#want} -ge 7 ] || { echo "merge_pinned: REFUSED — sha prefix must be >= 7 chars" >&2; exit 2; }
R=(); [ -n "$repo" ] && R=(-R "$repo")
[ -z "$base" ] && [ -z "$repo" ] && base="fable/substrate-safe-fixes"

head=$(gh pr view "$pr" ${R[@]+"${R[@]}"} --json headRefOid -q .headRefOid)
case "$head" in
  "$want"*) ;;
  *) echo "merge_pinned: REFUSED — head is ${head:0:12}, not the reviewed $want" >&2; exit 2 ;;
esac

if [ -n "$base" ]; then
  got=$(gh pr view "$pr" ${R[@]+"${R[@]}"} --json baseRefName -q .baseRefName)
  [ "$got" = "$base" ] || { echo "merge_pinned: REFUSED — base is $got, expected $base" >&2; exit 2; }
fi

checks=$(gh pr checks "$pr" ${R[@]+"${R[@]}"} --json name,bucket 2>/dev/null || true)
n=$(printf '%s' "$checks" | python3 -c 'import json,sys; print(len(json.loads(sys.stdin.read() or "[]")))')
[ "$n" -gt 0 ] || { echo "merge_pinned: REFUSED — no CI checks reported" >&2; exit 2; }
bad=$(printf '%s' "$checks" | python3 -c '
import json,sys
rows = json.loads(sys.stdin.read())
print("; ".join("%s=%s" % (c["name"], c["bucket"]) for c in rows if c["bucket"] not in ("pass", "skipping")))')
[ -z "$bad" ] || { echo "merge_pinned: REFUSED — checks not green: $bad" >&2; exit 2; }

gh pr merge "$pr" ${R[@]+"${R[@]}"} --squash --match-head-commit "$head"
echo "merge_pinned: MERGED $pr at ${head:0:12} ($n checks green)"
