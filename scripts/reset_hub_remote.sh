#!/usr/bin/env bash
# reset_hub_remote.sh — cross-host "clear the hub" for the Fleet-controls button.
#
# The button's callback runs in the ingest daemon ON THE MINI, but since the
# 2026-07-31 relocation the hub body lives on the VPS (wingmen-core), tmux `orch`
# as user `wingmen`. The old wiring ran scripts/reset_orch.sh LOCALLY on the Mini,
# where it correctly refused (ORCH_BODY_ROLE=console / no local `orch` session) —
# i.e. the hub button was silently broken (worse than none). op#8870.
#
# This wrapper SSHes to the VPS and runs reset_orch.sh THERE — the hub's home turf:
# ORCH_BODY_ROLE=hub, the `orch` tmux is local, and reset_orch.sh's own safety
# (fresh-handoff/checkpoint gate, BUSY-refuse, composer-preserve, safe /clear→boot)
# all apply. No safety is duplicated or bypassed here; we only relocate WHERE the
# canonical reset runs.
set -euo pipefail
KEY="${WINGMEN_VPS_KEY:-$HOME/.ssh/wingmen_vps}"
VPS="${WINGMEN_VPS_HOST:-91.107.235.77}"
[ -f "$KEY" ] || { echo "reset_hub_remote: VPS key $KEY not found" >&2; exit 9; }
# SAFETY GUARD (Nazim 39292/39435, 2026-09-12): the hub RELOCATED (wingmen-core -> gzb,
# 2026-09-05). This wrapper SSHes to $VPS and resets THERE; if the current orch_lease holder
# is NOT on $VPS, that would reset the WRONG / decommissioned box. Resolve holder_host and
# REFUSE unless $VPS is the current holder. FAIL-CLOSED: any uncertainty (no DATABASE_URL,
# unknown holder, resolver import error) => refuse — resetting a stale host is the exact
# failure this guards. Reuses scripts/lib/hub_reach.is_reach_host_current (TDD'd).
ORCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
_HOLDER="$(psql "${DATABASE_URL:-}" -tAc "SELECT holder_host FROM orch_lease WHERE lease_key='orch-hub'" 2>/dev/null | tr -d '[:space:]')"
if ! python3 -c "import sys; sys.path.insert(0,'$ORCH_DIR'); from scripts.lib import hub_reach as h; sys.exit(0 if h.is_reach_host_current('$VPS', ('$_HOLDER' or None)) else 1)" 2>/dev/null; then
  echo "[reset_hub_remote] ABORT (safety guard): orch_lease holder_host='${_HOLDER:-<unknown>}' is NOT on target $VPS. The hub likely relocated (e.g. gzb); resetting $VPS could clear a decommissioned/wrong session. Refusing. Reach the CURRENT host instead — see scripts/lib/hub_reach.py." >&2
  exit 7
fi
echo "[reset_hub_remote] clearing the hub on the VPS ($VPS) via reset_orch.sh..."
# RESET_DRYRUN must cross the ssh boundary explicitly: env vars do NOT propagate through
# ssh, so a caller who set it locally would otherwise get a REAL hub reset from what they
# believed was a dry run — the silent-safeguard failure this pair was fixed for on
# 2026-08-15. Forwarded as an explicit assignment on the remote command line.
_DRY="${RESET_DRYRUN:-0}"
[ "$_DRY" = 1 ] && echo "[reset_hub_remote] RESET_DRYRUN=1 — forwarding to the VPS; gates evaluated, hub NOT cleared."
exec ssh -i "$KEY" -o ConnectTimeout=15 -o BatchMode=yes "root@$VPS" \
  "sudo -u wingmen bash -lc \"cd ~/wingmen/orchestrator && exec env RESET_DRYRUN=$_DRY bash scripts/reset_orch.sh\""
