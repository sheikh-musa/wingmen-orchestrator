#!/usr/bin/env bash
# Path 2 — install Tailscale as a login-INDEPENDENT system daemon (connects at boot,
# no GUI login required). Replaces the App Store Tailscale.app (sandboxed, GUI-only,
# cannot run at boot) for the headless Mac Mini fleet node.
#
#   Run:   sudo bash scripts/setup_tailscale_system_daemon.sh
#   Auth:  a login URL prints — open it in a browser to authorize the node.
#   Safety net if this drops your SSH/tunnel: Chrome Remote Desktop (rides Google's
#          relay, independent of Tailscale; works after boot because auto-login is on).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Must run as root:  sudo bash $0" >&2; exit 1
fi

TS_BIN="$(command -v tailscale  || echo /usr/local/bin/tailscale)"
TSD_BIN="$(command -v tailscaled || echo /usr/local/bin/tailscaled)"
[ -x "$TS_BIN" ]  || { echo "tailscale CLI not found — run: brew install tailscale"  >&2; exit 1; }
[ -x "$TSD_BIN" ] || { echo "tailscaled not found — run: brew install tailscale"      >&2; exit 1; }

echo "==> Installing tailscaled as a system LaunchDaemon (starts at boot, no login)…"
"$TSD_BIN" install-system-daemon

echo "==> Bringing the node up. A login URL will print below — open it in a browser to authorize."
"$TS_BIN" up --accept-routes

echo
echo "==> Current status:"
"$TS_BIN" status | head -6

cat <<'NEXT'

NEXT STEPS (only after you confirm SSH works over the new daemon):
  1. Quit + sign out of the App Store Tailscale.app  (avoid two clients fighting over one node).
  2. Disable the old GUI-launching user agent:
        launchctl bootout gui/$(id -u)/dev.wingmen.tailscale-up
  3. (optional) In the Tailscale admin console, remove/rename the old node so the daemon
     keeps the sheikhs-mac-mini-1 name/IP (100.83.21.34).

Verify boot-without-login worked:  reboot, DON'T log in, then from another device:
        ssh sheikhmusa@100.83.21.34      (should connect straight away)
NEXT
