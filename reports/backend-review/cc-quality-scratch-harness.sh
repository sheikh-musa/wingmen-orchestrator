#!/usr/bin/env bash
# =============================================================================
# cc-quality SCRATCH HARNESS — run governance-mechanism repros against a LOCAL
# throwaway Postgres, never the governance substrate.
#
# WHY THIS EXISTS (CAI-RESP-1017): I damaged production with my own repro suite.
# A section appended below the file's final ROLLBACK ran in autocommit, flipped
# my own audit verdict, and fired a false escalation to cai. ON_ERROR_STOP only
# NARROWS that hazard. cai's ruling is the durable fix: a test that CAN commit
# to prod IS the hazard. This harness removes the capability rather than the
# temptation — the cluster listens on a unix socket only, with listen_addresses
# empty, so it has no network path to the substrate at all.
#
# USAGE:   ./cc-quality-scratch-harness.sh [path-to-suite.sql]
# DEFAULT: cc-quality-repros-1007-1009-branchfix.sql (same directory)
#
# The only thing it ever reads from prod is a SCHEMA-ONLY dump (no rows, no
# writes), and only when bootstrapping an empty cluster.
# =============================================================================
set -euo pipefail

PGBIN="${PGBIN:-/usr/local/Cellar/postgresql@17/17.10/bin}"
SCRATCH="${SCRATCH:-/private/tmp/claude-501/cc-quality-pgscratch}"
SOCK="${SOCK:-/tmp/ccqs}"            # MUST stay short: socket paths cap at 103 bytes
PORT="${PORT:-55432}"
SUITE="${1:-$(dirname "$0")/cc-quality-repros-1007-1009-branchfix.sql}"
export LC_ALL=C LANG=C               # macOS: without this the postmaster dies
                                     # "became multithreaded during startup"

[ -x "$PGBIN/pg_ctl" ] || { echo "FATAL: no server binaries at $PGBIN"; exit 1; }

# --- 1. cluster -------------------------------------------------------------
if [ ! -d "$SCRATCH/data" ]; then
  echo "==> initdb (fresh throwaway cluster)"
  mkdir -p "$SCRATCH" "$SOCK"
  "$PGBIN/initdb" -D "$SCRATCH/data" -U postgres --auth=trust >/dev/null
fi
if ! "$PGBIN/pg_isready" -h "$SOCK" -p "$PORT" >/dev/null 2>&1; then
  echo "==> starting scratch cluster (unix socket only, no TCP listener)"
  mkdir -p "$SOCK"
  "$PGBIN/pg_ctl" -D "$SCRATCH/data" \
    -o "-p $PORT -k $SOCK -c listen_addresses=''" \
    -l "$SCRATCH/server.log" start >/dev/null
  sleep 2
fi
"$PGBIN/pg_isready" -h "$SOCK" -p "$PORT" >/dev/null || {
  echo "FATAL: cluster did not start; see $SCRATCH/server.log"; tail -5 "$SCRATCH/server.log"; exit 1; }

PSQL=("$PGBIN/psql" -h "$SOCK" -p "$PORT" -U postgres)

# --- 2. ISOLATION PROOF, asserted every run, not assumed ---------------------
LISTEN=$("${PSQL[@]}" -At -c "show listen_addresses")
if [ -n "$LISTEN" ]; then
  echo "FATAL: listen_addresses='$LISTEN' — the scratch cluster has a network"
  echo "       listener. Refusing to run: the whole point is that this harness"
  echo "       CANNOT reach the substrate."
  exit 1
fi
echo "==> isolation OK: listen_addresses empty (unix socket only)"

# --- 3. SCHEMA COMPLETENESS ASSERTION ---------------------------------------
# orch-console caught the defect this replaces: the bootstrap used
#   psql ... >/dev/null 2>&1 || true
# which discarded output AND exit status, so a failed schema load was INVISIBLE.
# I had already hit that bug (dependency order wrong -> triggers silently did not
# create) and fixed only the ORDER, not the SILENCE. It is worse than an ordinary
# swallowed error because THE OBJECTS THAT FAIL TO LOAD ARE THE GUARDS UNDER TEST:
# most sections assert that something does NOT fire, so a MISSING trigger and a
# WORKING trigger look identical. That is a false PASS on exactly what is being
# audited — ON_ERROR_STOP off again, one layer up, in bash.
# So: assert the object SET exists, and name what is missing. Asserting objects
# also catches a CREATE that no-ops or an object dropped by a later statement,
# which merely removing `|| true` would not.
EXPECTED_FUNCS="decision_audit_actor_norm decision_audit_conflict decision_audit_effective_builder decision_audit_required decision_audit_unresolved decision_audit_tier_candidate enforce_decision_audit_not_self enforce_decision_audit_resolution_independence escalate_stale_decision_audits enforce_challenge_window_timeouts close_decision_by_audit"
EXPECTED_TABLES="strategic_decisions decision_audits agent_messages orchestrator_runtime_config"
EXPECTED_TRIGGERS="trg_decision_audits_not_self trg_decision_audits_resolution_independence"
EXPECTED_VIEWS="decision_audit_state"

schema_missing() {   # prints missing object names, empty output == complete
  local miss=""
  for f in $EXPECTED_FUNCS; do
    [ "$("${PSQL[@]}" -At -c "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname='$f'" 2>/dev/null)" = "1" ] || miss="$miss function:$f"
  done
  for t in $EXPECTED_TABLES; do
    [ "$("${PSQL[@]}" -At -c "select count(*) from information_schema.tables where table_schema='public' and table_name='$t'" 2>/dev/null)" = "1" ] || miss="$miss table:$t"
  done
  for g in $EXPECTED_TRIGGERS; do
    [ "$("${PSQL[@]}" -At -c "select count(*) from pg_trigger where tgname='$g' and not tgisinternal" 2>/dev/null)" = "1" ] || miss="$miss trigger:$g"
  done
  for v in $EXPECTED_VIEWS; do
    [ "$("${PSQL[@]}" -At -c "select count(*) from pg_views where schemaname='public' and viewname='$v'" 2>/dev/null)" = "1" ] || miss="$miss view:$v"
  done
  echo "$miss"
}

# --- 4. schema, bootstrapped only if the object set is INCOMPLETE -------------
# (was: "does decision_audits exist" — one table. A partially-loaded cluster from
#  an earlier silent failure then skipped bootstrap forever and ran anyway.)

MISSING="$(schema_missing)"
if [ -n "$MISSING" ]; then
  echo "==> schema incomplete, (re)bootstrapping. missing:$MISSING"
  echo "==> bootstrapping schema from a READ-ONLY prod dump (schema only, no rows)"
  : "${DATABASE_URL:?set DATABASE_URL (schema-only read; this harness never writes to prod)}"
  BOOT="$SCRATCH/bootstrap.sql"
  cat > "$BOOT" <<'ROLES'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='console_readonly') THEN CREATE ROLE console_readonly NOLOGIN; END IF;
END $$;
ROLES
  # Each prod READ is checked explicitly. Without this, `set -e` kills the script
  # mid-bootstrap and the operator sees "(re)bootstrapping" followed by SILENCE —
  # safe, because the suite never runs, but silent, which is the very class of
  # defect this harness exists to remove. Found by testing the failure path
  # instead of assuming it.
  if ! pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges \
        -t public.strategic_decisions -t public.decision_audits \
        -t public.agent_messages -t public.orchestrator_runtime_config >> "$BOOT" 2>"$SCRATCH/dump.err"; then
    echo "FATAL: pg_dump of the prod schema FAILED — cannot bootstrap the scratch cluster."
    sed 's/^/         /' "$SCRATCH/dump.err" | tail -5
    echo "       REFUSING TO RUN THE SUITE against an incomplete schema."
    exit 1
  fi
  # Functions and the view must land AFTER the tables, and triggers AFTER the
  # functions they call — dependency order, learned by getting it wrong.
  if ! psql "$DATABASE_URL" -At -c "select string_agg(pg_get_functiondef(p.oid), E';\n\n')||';' from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname in ('decision_audit_actor_norm','decision_audit_conflict','decision_audit_effective_builder','decision_audit_required','decision_audit_unresolved','decision_audit_tier_candidate','enforce_decision_audit_not_self','enforce_decision_audit_resolution_independence','escalate_stale_decision_audits','enforce_challenge_window_timeouts','close_decision_by_audit')" >> "$BOOT" 2>>"$SCRATCH/dump.err"; then
    echo "FATAL: could not read function definitions from prod; see $SCRATCH/dump.err"; exit 1
  fi
  if ! psql "$DATABASE_URL" -At -c "select 'CREATE VIEW decision_audit_state AS '||pg_get_viewdef('decision_audit_state'::regclass,true)||';'" >> "$BOOT" 2>>"$SCRATCH/dump.err"; then
    echo "FATAL: could not read the view definition from prod; see $SCRATCH/dump.err"; exit 1
  fi
  # Errors are LOGGED, not discarded. Some are expected on a first pass (triggers
  # whose functions are not defined yet), which is exactly why the completeness
  # ASSERTION below — not the exit status — is what decides success.
  "${PSQL[@]}" -q -f "$BOOT"                   > "$SCRATCH/bootstrap.log" 2>&1 || true
  grep -E "^CREATE TRIGGER" "$BOOT" > "$SCRATCH/triggers.sql" || true
  "${PSQL[@]}" -q -f "$SCRATCH/triggers.sql"  >> "$SCRATCH/bootstrap.log" 2>&1 || true

  STILL_MISSING="$(schema_missing)"
  if [ -n "$STILL_MISSING" ]; then
    echo "FATAL: schema bootstrap INCOMPLETE. Missing objects:"
    for m in $STILL_MISSING; do echo "         - $m"; done
    echo "       Errors are in $SCRATCH/bootstrap.log (last 10 lines):"
    grep -iE "^(psql:)?.*ERROR" "$SCRATCH/bootstrap.log" | tail -10 | sed 's/^/         /'
    echo "       REFUSING TO RUN THE SUITE. A missing guard and a working guard look"
    echo "       identical to an assertion expecting zero — that is a false PASS on"
    echo "       exactly the objects under audit."
    exit 1
  fi
  echo "==> schema complete: all $(echo $EXPECTED_FUNCS | wc -w | tr -d ' ') functions, $(echo $EXPECTED_TABLES | wc -w | tr -d ' ') tables, $(echo $EXPECTED_TRIGGERS | wc -w | tr -d ' ') triggers, $(echo $EXPECTED_VIEWS | wc -w | tr -d ' ') view present"
fi

# --- 4. run -----------------------------------------------------------------
# --- FIXTURE PRECONDITION -----------------------------------------------------
# orch noticed EXPECTED_TABLES carries orchestrator_runtime_config and called it
# "harmless, arguably right". It is stronger than that: it is LOAD-BEARING, and
# the reason is a false-green channel neither of us had named.
# enforce_challenge_window_timeouts reads challenge_enforcer_mode from that table
# and DEFAULTS TO 'dry_run' when the row is absent. Section B's expected BEFORE is
# `flipped` -> accepted_by_timeout, which only happens in write_mode. On a cluster
# whose schema is complete but whose fixture row is missing, section B returns
# 'logged' instead — a DIFFERENT result produced by a MISSING FIXTURE rather than
# by the code under test, and a skimming reader could take it as "the tier-drop
# defect is gone". So the precondition is asserted, not assumed.
MODE="$("${PSQL[@]}" -At -c "select value from orchestrator_runtime_config where key='challenge_enforcer_mode'" 2>/dev/null || true)"
if [ "$MODE" != "write_mode" ]; then
  echo "FATAL: challenge_enforcer_mode='${MODE:-<unset>}' in the scratch cluster."
  echo "       Section B needs write_mode: the enforcer defaults to dry_run when this"
  echo "       row is missing, and would return 'logged' instead of 'flipped' — a"
  echo "       result produced by a missing fixture, not by the code under test."
  echo "       Seed it:  insert into orchestrator_runtime_config(key,value)"
  echo "                 values ('challenge_enforcer_mode','write_mode');"
  exit 1
fi
echo "==> fixture precondition OK: challenge_enforcer_mode=write_mode"

PRERUN_MISSING="$(schema_missing)"
if [ -n "$PRERUN_MISSING" ]; then
  echo "FATAL: object set incomplete at run time. Missing:$PRERUN_MISSING"
  echo "       (something dropped these after bootstrap. Refusing to run.)"
  exit 1
fi
echo "==> pre-run assertion OK: every guard under test is present"
echo "==> running: $SUITE"
"${PSQL[@]}" -f "$SUITE"

echo
echo "==> done. Cluster still running on $SOCK:$PORT."
echo "    stop:  $PGBIN/pg_ctl -D $SCRATCH/data stop"
echo "    nuke:  $PGBIN/pg_ctl -D $SCRATCH/data stop; rm -rf $SCRATCH"
