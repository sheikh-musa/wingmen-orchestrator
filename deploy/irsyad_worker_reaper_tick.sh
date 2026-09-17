#!/bin/bash
# irsyad_worker_reaper_tick.sh — one worker-reaper pass for cron (Musa op#20774 / Nazim #40833).
# Tears down WOUND-DOWN elastic workers (idle + latest bus post = wind-down, past grace) so the
# autoscaler pool count SHRINKS and re-proposes on the next demand — fixes "only built when
# prompted". Never reaps a busy/working worker (pane_busy footer check) or a standing lane/coord.
set -uo pipefail
cd /home/gazzai/wingmen/orchestrator || exit 1
set -a; . /dev/shm/wingmen-secrets/.env 2>/dev/null; set +a
exec /home/gazzai/wingmen/orchestrator/.venv/bin/python3 scripts/irsyad_worker_reaper.py --once
