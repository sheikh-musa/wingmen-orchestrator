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
[ -f "$KEY" ] || { echo "reset_hub_remote: VPS key $KEY not found" >&2; exit 9; }
# SAFETY GUARD (Nazim 39292/39435, 2026-09-12): the hub RELOCATED (wingmen-core -> gzb,
# 2026-09-05). This wrapper SSHes to a single target and resets THERE; if the current
# orch_lease holder is NOT that target, that would reset the WRONG / decommissioned box.
# op#42933 (2026-09-24): the VPS default used to be the bare hardcoded wingmen-core IP —
# resolve it from hub_reach instead, so a stale literal never becomes the SSH target even
# transiently. FAIL-CLOSED: any uncertainty (no DATABASE_URL, unknown holder, resolver
# import error) => refuse — resetting a stale host is the exact failure this guards.
# Reuses scripts/lib/hub_reach (TDD'd).
ORCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
_HOLDER="$(psql "${DATABASE_URL:-}" -tAc "SELECT holder_host FROM orch_lease WHERE lease_key='orch-hub'" 2>/dev/null | tr -d '[:space:]')"
# WINGMEN_VPS_HOST, if set, is an explicit operator override and always wins. Otherwise
# derive the target from the resolved holder: this script only knows how to reach the
# wingmen-core canonical group directly (a single `ssh root@host` hop) — it has no gzb
# reach at all (op#42907/op#20655: gzb's old relay, gzb-vpn.sh via this same VPS, is
# dead and was never replaced with an interactive path — see scripts/lib/hub_reach.py),
# so an unset/unknown/gzb-resolved holder must refuse with a clear pointer, never guess
# a host this script can't actually act on correctly.
if [ -n "${WINGMEN_VPS_HOST:-}" ]; then
  VPS="$WINGMEN_VPS_HOST"
else
  VPS="$(python3 -c "
import sys
sys.path.insert(0, '$ORCH_DIR')
from scripts.lib import hub_reach as h
info = h.hub_reach_for_holder('$_HOLDER' or None)
print(info['host'] if info['known'] and info['host'] == 'wingmen-core' else '')
" 2>/dev/null)"
  if [ -z "$VPS" ]; then
    echo "[reset_hub_remote] ABORT (safety guard): orch_lease holder_host='${_HOLDER:-<unknown>}' does not resolve to the wingmen-core canonical group (this script only supports a direct single-hop SSH to wingmen-core, not gzb's multi-hop reach). Refusing rather than guessing — see scripts/lib/hub_reach.py for the current reach path, or set WINGMEN_VPS_HOST explicitly if you really mean to target a specific host." >&2
    exit 7
  fi
  VPS="91.107.235.77"  # canonical group resolved to wingmen-core; this is its one known address
fi
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
