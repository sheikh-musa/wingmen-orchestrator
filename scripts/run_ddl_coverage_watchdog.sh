#!/usr/bin/env bash
# run_ddl_coverage_watchdog.sh — cc-fleet-health (SRE) launchd wrapper for
# scripts/ddl_coverage_watchdog.py (cc-substrate's detect-only DDL-coverage
# watchdog, op#22669 item 3). Scheduling this watchdog is pen (iii) under the
# fleet_health_lease (ORCH-TOPOLOGY-001 / CAI-RESP-501; orch-console #44153).
#
# WHY a wrapper: the script REQUIRES explicit --silo-dsn/--bus-dsn (no ambient
# $DATABASE_URL fallback, by design — bus #44135/#44153). Passing the DSN in the
# plist would write the secret into a world-readable ~/Library/LaunchAgents file.
# This wrapper keeps the secret in the orchestrator .env, sets PYTHONPATH + the
# vault-audit identity, and passes the args. Two silos:
#   substrate <- --silo-dsn "$DATABASE_URL" (silo==bus, the one legit same-DSN case)
#   cosem     <- --silo-dsn-vault-key cosem_platform_watch_dsn (read-only vaulted DSN)
# Both page onto the SUBSTRATE bus, never into the watched silo. Detect-only;
# exit 1 = unledgered-DDL drift (page), 0 = clean, 2 = usage.
#
# Committed (this file + launchd/dev.wingmen.ddl-coverage-watchdog-{substrate,ywrpt}.plist)
# so the config survives a Mini disk loss (bus #44181/#44191). The plists point at THIS
# path ($HOME/wingmen/orchestrator/scripts/), which a repo restore recreates.
set -euo pipefail

ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a          # DATABASE_URL (substrate == bus)
export PYTHONPATH="$ORCH_DIR:$ORCH_DIR/scripts:$ORCH_DIR/scripts/lib"
export AGENT_ID=cc-fleet-health CC_BASE_AGENT_ID=cc-fleet-health   # vault-access audit identity
PY="$ORCH_DIR/.venv/bin/python3"
WD="$ORCH_DIR/scripts/ddl_coverage_watchdog.py"

case "${1:-}" in
  substrate)
    exec "$PY" "$WD" --silo tscuymavysscrvoberrr \
      --silo-dsn "$DATABASE_URL" --bus-dsn "$DATABASE_URL" --once ;;
  cosem)
    exec "$PY" "$WD" --silo ywrpttpxwfcoodovxhsr \
      --silo-dsn-vault-key cosem_platform_watch_dsn --bus-dsn "$DATABASE_URL" --once ;;
  *)
    echo "usage: $0 {substrate|cosem}" >&2; exit 2 ;;
esac
