#!/usr/bin/env bash
# scripts/ci/check_lock_keys.sh — advisory-lock namespace enforcement.
#
# SA2 (CAI-RESP-054): docs/lock-namespace.md is docs-only without mechanical
# enforcement. An engineer who adds pg_advisory_lock(1234) without opening
# the registry silently collides with whoever else picked 1234. Silent
# collision is hard to diagnose later. Grep is cheap insurance.
#
# Rules enforced (per docs/lock-namespace.md):
#   R1. No hashtext(…) calls anywhere — 32-bit hash space loses registry meaning.
#   R2. Every literal integer passed to pg_(try_)?advisory_(xact_)?(un)?lock(
#       MUST appear in the "Active registrations" table in docs/lock-namespace.md.
#
# Exit 0 on pass, 1 on violation. Pass --root <dir> to test against a fixture.

set -euo pipefail

ROOT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$ROOT" ]; then
    ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi

REGISTRY="$ROOT/docs/lock-namespace.md"
SELF="scripts/ci/check_lock_keys.sh"
# The script's own test file contains literal forbidden strings as fixtures
# — it exercises the enforcer, and its presence is proof the enforcer works.
SELF_TEST="tests/test_check_lock_keys.py"

if [ ! -f "$REGISTRY" ]; then
    echo "check_lock_keys: registry not found at $REGISTRY" >&2
    exit 2
fi

# ── Extract registered IDs from the "Active registrations" table ──────────────
# Table rows look like: | 1001   | AGENT_ID_ALLOC    | owner | purpose |
# Keep only lines whose first pipe-delimited cell is a pure integer.
registered="$(awk -F'|' '
    /^## Active registrations/ { in_tbl = 1; next }
    /^## /                      { in_tbl = 0 }
    in_tbl && NF >= 2 {
        v = $2
        gsub(/[[:space:]]/, "", v)
        if (v ~ /^[0-9]+$/) print v
    }
' "$REGISTRY")"

if [ -z "$registered" ]; then
    echo "check_lock_keys: no registered IDs parsed from $REGISTRY" >&2
    exit 2
fi

fail=0

# ── R1. Reject hashtext( anywhere in source tree ──────────────────────────────
# Exclude the registry (which quotes 'hashtext' in prose) and this script.
if hits=$(grep -rn --include='*.py' --include='*.sh' --include='*.sql' \
    'hashtext(' "$ROOT" \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=node_modules \
    --exclude-dir=docs 2>/dev/null); then
    # Filter out self-reference and the enforcer's own test fixtures
    hits=$(echo "$hits" | grep -v -e "$SELF" -e "$SELF_TEST" || true)
    if [ -n "$hits" ]; then
        echo "check_lock_keys: R1 violation — hashtext( found (32-bit hash defeats registry):" >&2
        echo "$hits" >&2
        echo "  → use an explicit bigint literal registered in docs/lock-namespace.md" >&2
        fail=1
    fi
fi

# ── R2. Every literal int in pg_*_advisory_*lock( must be registered ──────────
# Regex captures calls like pg_try_advisory_xact_lock(1001) or pg_advisory_lock( 1001 ).
lock_hits=$(grep -rnE --include='*.py' --include='*.sh' --include='*.sql' \
    'pg_(try_)?advisory_(xact_)?(un)?lock[[:space:]]*\([[:space:]]*[0-9]+[[:space:]]*[,)]' \
    "$ROOT" \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=node_modules \
    --exclude-dir=docs 2>/dev/null || true)

# Filter self + enforcer's own test fixtures
lock_hits=$(echo "$lock_hits" | grep -v -e "$SELF" -e "$SELF_TEST" || true)

if [ -n "$lock_hits" ]; then
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        # Extract the first integer argument after the lock-call paren.
        # Two-step: (1) isolate the call+literal up through the first digit run,
        # (2) pull the digits from that prefix (which now ends with them).
        call_prefix=$(echo "$line" | grep -oE 'pg_(try_)?advisory_(xact_)?(un)?lock[[:space:]]*\([[:space:]]*[0-9]+' | head -n 1)
        id=$(echo "$call_prefix" | grep -oE '[0-9]+' | tail -n 1)
        if [ -z "$id" ]; then continue; fi
        if ! printf '%s\n' "$registered" | grep -qE "^${id}$"; then
            echo "check_lock_keys: R2 violation — advisory-lock key $id not registered:" >&2
            echo "  $line" >&2
            echo "  → add a row for $id to docs/lock-namespace.md 'Active registrations'" >&2
            fail=1
        fi
    done <<< "$lock_hits"
fi

if [ "$fail" -ne 0 ]; then
    exit 1
fi

echo "check_lock_keys: OK (all lock keys registered; no hashtext)"
exit 0
