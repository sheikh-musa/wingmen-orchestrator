#!/usr/bin/env bash
# boot_wake_backstop_sweep.sh — launchd wrapper for the wake BACKSTOP sweep
# (op#11297, cc-quality #16827/#16847). The reliability FLOOR under the realtime
# agent_wake_subscriber: a periodic reconciler that re-wakes any recipient with a
# directed row rotting unread, independent of the realtime WS (which stalled
# silently ~7.5h on 2026-08-08). One instance per host (Mini + VPS-for-hub).
# KeepAlive-managed; peer of boot_agent_wake_subscriber.sh.
#
# Runs the module as a DIRECT FILE (not -m) so its script dir is on sys.path and
# `import agent_wake` resolves. Honors AUTO_WAKE_ENABLED (sourced from .env): the
# sweep observes-only (dry) when the kill-switch is off — same arming as the
# realtime subscriber. Reversible: `launchctl bootout gui/$(id -u)/dev.wingmen.wake-backstop-sweep`.
set -euo pipefail
ORCH_DIR="$HOME/wingmen/orchestrator"
cd "$ORCH_DIR"
set -a; . ./.env; set +a
exec "$ORCH_DIR/.venv/bin/python3" "$ORCH_DIR/nervous_system/wake_backstop_sweep.py"
