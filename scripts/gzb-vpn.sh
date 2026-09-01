#!/usr/bin/env bash
# gzb-vpn.sh — SPLIT-TUNNEL FortiGate SSL-VPN to reach the gzb LAN from wingmen-core.
#
# HARD INVARIANT (the operator's constraint): wingmen-core's DEFAULT ROUTE is NEVER
# changed. We route ONLY the gzb subnet through the tunnel. If anything would touch
# the default route, we abort + tear down. A dead-man timer auto-tears-down so a
# stuck tunnel self-heals even if the caller's session freezes.
#
# This is the opposite of the full-tunnel that broke the Mini twice (2026-08-27/28).
#
# Install (Nazim/root on wingmen-core):
#   sudo apt-get install -y openfortivpn
#   sudo install -o root -g root -m 0755 gzb-vpn.sh /usr/local/sbin/gzb-vpn.sh
#   sudo install -o root -g root -m 0600 /dev/stdin /etc/gzb-vpn.conf   # paste creds (see below)
#   echo 'wingmen ALL=(root) NOPASSWD: /usr/local/sbin/gzb-vpn.sh' | sudo tee /etc/sudoers.d/gzb-vpn
#   sudo chmod 0440 /etc/sudoers.d/gzb-vpn
#
# /etc/gzb-vpn.conf (root-only, creds from op#17158/17168 — NEVER on the bus/TG):
#   host = 66.96.212.114
#   port = 10443
#   username = <portal user>
#   password = <portal pass>
#   trusted-cert = 6dde56b6c8793177522ebd5d4f358f952a40f7e7d5518bbd2e17e553c576bf48
#
# Usage (hub, via sudo):  sudo gzb-vpn.sh up | down | status
set -euo pipefail

CONF=/etc/gzb-vpn.conf
GZB_SUBNET=192.168.1.0/24            # ONLY this goes through the tunnel
EXPECT_DEFAULT_RE='^default via 172\.31\.1\.1 dev eth0'   # our lifeline — must never change
FALLBACK_DEFAULT='default via 172.31.1.1 dev eth0'
DEADMAN_SECONDS="${DEADMAN_SECONDS:-900}"   # 15 min hard ceiling; override per-invocation
RUN=/run/gzb-vpn
PIDFILE="$RUN/ofv.pid"
DEADMANFILE="$RUN/deadman.pid"
IFFILE="$RUN/tunif"
LOG=/var/log/gzb-vpn.log

log(){ echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG" >&2; }
mkdir -p "$RUN"

default_ok(){ ip route show default | grep -qE "$EXPECT_DEFAULT_RE"; }

restore_default(){
  if ! default_ok; then
    log "SAFETY-NET: default route not as expected — restoring $FALLBACK_DEFAULT"
    ip route replace $FALLBACK_DEFAULT || true
  fi
}

down(){
  log "teardown starting"
  # kill the dead-man first so it doesn't double-fire
  [ -f "$DEADMANFILE" ] && kill "$(cat "$DEADMANFILE")" 2>/dev/null || true; rm -f "$DEADMANFILE"
  # remove our scoped route (if any)
  if [ -f "$IFFILE" ]; then TUNIF="$(cat "$IFFILE")"; ip route del "$GZB_SUBNET" dev "$TUNIF" 2>/dev/null || true; fi
  # SIGTERM ALL openfortivpn (reap-all — single-tunnel design; robust vs clobbered PIDFILE
  # from any concurrent/killed run. graceful TERM restores routes; SIGKILL does NOT — Nazim's gotcha).
  if pgrep -x openfortivpn >/dev/null 2>&1; then
    pkill -TERM -x openfortivpn 2>/dev/null || true
    for i in $(seq 1 10); do pgrep -x openfortivpn >/dev/null 2>&1 || break; sleep 1; done
    pgrep -x openfortivpn >/dev/null 2>&1 && { log "still up after 10s TERM — last-resort KILL + manual route restore"; pkill -KILL -x openfortivpn 2>/dev/null || true; }
  fi
  rm -f "$PIDFILE"
  rm -f "$IFFILE"
  restore_default            # ALWAYS end with the lifeline intact
  log "teardown complete; default route: $(ip route show default | tr '\n' ' ')"
}

up(){
  # 1) refuse to start unless the default route is exactly what we expect
  default_ok || { log "ABORT: unexpected default route pre-connect: $(ip route show default)"; exit 1; }
  [ -f "$CONF" ] || { log "ABORT: $CONF missing (Nazim places creds)"; exit 1; }
  # 2) snapshot interfaces so we can identify the tunnel iface openfortivpn creates
  before="$(ip -o link show | awk -F': ' '{print $2}' | sed 's/@.*//')"
  # 3) raise WITHOUT letting it touch routes or DNS (split tunnel)
  log "raising split-tunnel (openfortivpn --no-routes --no-dns)"
  setsid openfortivpn -c "$CONF" --no-routes --no-dns >>"$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  # 4) wait for a new point-to-point iface (ppp0 / tun) to appear
  TUNIF=""
  for i in $(seq 1 20); do
    sleep 1
    after="$(ip -o link show | awk -F': ' '{print $2}' | sed 's/@.*//')"
    TUNIF="$(comm -13 <(echo "$before"|sort) <(echo "$after"|sort) | grep -E '^(ppp|tun|vpn)' | head -1 || true)"
    [ -n "$TUNIF" ] && break
  done
  [ -n "$TUNIF" ] || { log "ABORT: tunnel iface never appeared"; down; exit 1; }
  echo "$TUNIF" > "$IFFILE"
  log "tunnel iface = $TUNIF"
  # 5) add ONLY the gzb subnet via the tunnel — never the default route
  # 4b) WAIT for the iface to be routable (pppd IPCP done) — ppp0 APPEARS before it is up (Nazim caught this)
  for i in $(seq 1 20); do ip addr show "$TUNIF" 2>/dev/null | grep -q "inet " && break; sleep 1; done
  # add ONLY the gzb subnet — retry (transient "nexthop not up" until pppd finishes)
  for i in $(seq 1 10); do ip route add "$GZB_SUBNET" dev "$TUNIF" 2>/dev/null && break; sleep 1; done
  ip route show "$GZB_SUBNET" | grep -q . || { log "ABORT: gzb route not added after wait"; down; exit 1; }
  # 6) VERIFY the default route is untouched; if not, abort+teardown immediately
  if ! default_ok; then log "ABORT: default route CHANGED after connect — tearing down"; down; exit 1; fi
  # 7) arm the dead-man auto-teardown (self-heal ceiling)
  ( sleep "$DEADMAN_SECONDS"; log "DEAD-MAN fired ($DEADMAN_SECONDS s) — auto-teardown"; down ) &
  echo $! > "$DEADMANFILE"
  log "UP: gzb subnet $GZB_SUBNET via $TUNIF; default route intact; dead-man ${DEADMAN_SECONDS}s armed"
  ip route get 192.168.1.114 | head -1
}

status(){
  echo "default: $(ip route show default | tr '\n' ' ')"
  [ -f "$IFFILE" ] && echo "tunif: $(cat "$IFFILE")  gzb-route: $(ip route show "$GZB_SUBNET" 2>/dev/null)"
  [ -f "$PIDFILE" ] && { kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo "openfortivpn: UP (pid $(cat "$PIDFILE"))" || echo "openfortivpn: pidfile stale"; } || echo "openfortivpn: down"
  [ -f "$DEADMANFILE" ] && echo "dead-man: armed (pid $(cat "$DEADMANFILE"))"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) status ;;
  *) echo "usage: $0 up|down|status" >&2; exit 2 ;;
esac
