#!/usr/bin/env bash
# Hourly run of the irsyad PII-containment monitor (moved off the Mac Mini, console #81299 A / #81357).
#
# TRACKED (orch-console #89139). This wrapper used to live untracked on gzb, so editing the
# cron entry point was invisible to review and to `git pull`. On 2026-11-01 a comment edit
# rewrote it atomically and dropped the executable bit; cron then failed with "Permission
# denied" and the 05:23Z tick never started -- one hour of a minors'-PII monitor silently
# not running. Tracked here, `git pull` IS the deploy, and tests/test_pii_monitor_tick_wrapper.py
# fails if this file loses mode 100755 in git.
#
# EXIT CODES (post orchestrator#800) -- see exit_code_for() in the monitor:
#   0 = this tier paged nothing (all zero, OR an accepted/already-reported state inside its ceiling)
#   1 = a breach that actually paged
#   2 = could-not-measure / connect-failed / missing credential
# The FORBIDDEN and residue gates page off the exit code, so a page can ride the bus beside a 0.
# Never read exit 0 as "no page was sent". NEVER run --accept here.
set -uo pipefail

# Resolve the checkout from this script's own location, so the same file works wherever the
# repo is cloned and the cron line can call the tracked path directly.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

# Host secrets (gzb keeps them outside the repo). Optional: the monitor's main() also falls
# back to the checkout's own .env, so a host without this file still runs.
ENV_FILE="${PII_MONITOR_ENV_FILE:-/dev/shm/wingmen-secrets/.env}"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

echo "=== $(date -u +%FT%TZ)"
"$PY" "$ROOT/scripts/irsyad_pii_containment_monitor.py"
rc=$?
echo "exit=$rc"
# Propagate it too. Until now the trailing echo WAS the script's exit status, so this wrapper
# always exited 0 no matter what the monitor said -- the log line was the only place the real
# code existed. Cheap to stop lying to any caller that ever checks.
exit "$rc"
