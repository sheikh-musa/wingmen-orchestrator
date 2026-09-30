#!/usr/bin/env bash
# pytest_local.sh — run the test suite SAFELY on a fleet host (backlog#68, Nazim #46576).
#
# WHY THIS EXISTS: the root conftest.py prod-ref guard REFUSES any pytest session whose
# effective DATABASE_URL points at a production store. On gzb/the Mini the shared .env
# holds the prod substrate DSN, so a bare `pytest` self-aborts (that guard exists because
# a local run once tripped the substrate pooler circuit breaker, bus #46566). Use THIS
# wrapper for local TDD.
#
# PRECEDENCE (conftest): the ENV VAR wins over the .env file. So exporting a LOCAL
# DATABASE_URL here makes the guard ALLOW the run (it never consults the prod .env), and
# the DB-integration tests run against YOUR local Postgres. Leave it UNSET (the safe
# default below) and the guard allows + the DB-integration tests SKIP.
#
# Usage:  scripts/pytest_local.sh [pytest args...]      e.g.  scripts/pytest_local.sh tests/test_foo.py -q
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# ── Option A (DB-integration TDD): point at YOUR LOCAL Postgres. Uncomment + edit the
#    DSN to your instance. It MUST be a local/ephemeral DB — NEVER a prod ref
#    (tscuymavysscrvoberrr / ceayjeamtmcyzzvqflus / goumlynecruxrlmzlntp); the guard
#    refuses those anyway. The Mini already runs postgres@17 test instances.
# export DATABASE_URL="postgresql://postgres@localhost:5432/wingmen_test"   # <-- set to your local DB
#
# ── Option B (safe default): DATABASE_URL UNSET -> guard allows -> DB-integration tests
#    SKIP; unit/logic tests run. Unset any inherited value so a stale prod DSN can't leak in.
if [ -z "${DATABASE_URL:-}" ]; then
    unset DATABASE_URL SUPABASE_DB_URL 2>/dev/null || true
fi

exec .venv/bin/python3 -m pytest "$@"
