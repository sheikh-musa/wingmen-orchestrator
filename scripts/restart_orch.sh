#!/usr/bin/env bash
# scripts/restart_orch.sh — safe orchestrator restart via launchd.
# See docs/runbooks/restart.md. Never invoke with nohup/disown/&.
set -euo pipefail

# Refuse nohup: bash's `trap -p HUP` doesn't surface inherited SIG_IGN, so
# probe SIGHUP disposition via python (signal.getsignal == SIG_IGN means
# something upstream ignored it — nohup, disown, or similar).
hup_ignored=0
if command -v python3 >/dev/null 2>&1 && \
   python3 -c 'import signal,sys; sys.exit(0 if signal.getsignal(signal.SIGHUP)==signal.SIG_IGN else 1)' 2>/dev/null; then
    hup_ignored=1
elif [ -n "$(trap -p HUP || true)" ]; then
    hup_ignored=1
fi
if [ "${hup_ignored}" -eq 1 ]; then
    echo "ERROR: refusing to run — invoked under nohup (SIGHUP is ignored)." >&2
    echo "       launchd already supervises the orchestrator. Run directly:" >&2
    echo "         ./scripts/restart_orch.sh" >&2
    exit 1
fi

LABEL="${ORCH_LAUNCHD_LABEL:-dev.wingmen.orchestrator}"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_ERR="${ORCH_LOG_ERR:-/Users/sheikhmusa/wingmen/orchestrator/logs/orch.err}"

print_recovery() {
    echo "" >&2
    echo "Manual recovery:" >&2
    echo "  launchctl bootout ${SERVICE} || true   # force stop" >&2
    echo "  rm -f /Users/sheikhmusa/wingmen/orchestrator/*.pid   # clear stale pid" >&2
    echo "  launchctl bootstrap ${DOMAIN} ${PLIST}   # reload" >&2
}

trap 'print_recovery' ERR

echo "== [1/5] current launchctl state for ${SERVICE} =="
launchctl print "${SERVICE}" 2>/dev/null | head -n 40 \
    || echo "(service not currently loaded)"

echo
echo "== [2/5] pre-flight: running jobs =="
if command -v supabase >/dev/null 2>&1 && [ -n "${SUPABASE_DB_URL:-}" ]; then
    supabase db execute --db-url "${SUPABASE_DB_URL}" \
        "SELECT id, repo_name, status FROM jobs WHERE status = 'running' LIMIT 10;" \
        || echo "WARN: supabase query failed — proceeding anyway"
else
    echo "WARN: no supabase db url / cli — skipping running-jobs check." \
         "Inspect /jobs in Telegram after restart."
fi

echo
echo "== [3/5] kickstart (atomic stop-and-restart) =="
launchctl kickstart -k "${SERVICE}"

echo
echo "== [4/5] polling for running pid (up to 15s) =="
ok=0
for i in $(seq 1 15); do
    state="$(launchctl print "${SERVICE}" 2>/dev/null \
        | awk -F'=' '/^[[:space:]]*state/{gsub(/[[:space:]]/,"",$2); print $2; exit}')"
    pid="$(launchctl print "${SERVICE}" 2>/dev/null \
        | awk -F'=' '/^[[:space:]]*pid/{gsub(/[[:space:]]/,"",$2); print $2; exit}')"
    if [ "${state:-}" = "running" ] && [ -n "${pid:-}" ] && [ "${pid}" != "0" ]; then
        echo "OK: running (pid ${pid}) after ${i}s"
        ok=1
        break
    fi
    sleep 1
done
if [ "${ok}" -ne 1 ]; then
    echo "ERROR: orchestrator did not reach running state within 15s" >&2
    exit 1
fi

echo
echo "== [5/5] tail -n 20 ${LOG_ERR} =="
if [ -f "${LOG_ERR}" ]; then
    tail -n 20 "${LOG_ERR}" || true
else
    echo "(no ${LOG_ERR} yet — orchestrator may still be initialising)"
fi

trap - ERR
echo
echo "Restart complete."
