#!/usr/bin/env bash
# pin_fleet_host_id.sh — SOURCE this from a body's boot script to export a stable
# FLEET_HOST_ID for that process (CAI-RESP-1436). One reviewed artifact, sourced by
# every boot, so no host-scoped read (agent_status writes, lease take/renew, in-process
# watchdog matchers) rides the flappy live socket.gethostname() (bus 41834).
#
# Contract: the sourcing boot must already have exported DATABASE_URL (or SUPABASE_DB_URL)
# and defined VENV_PY + ORCH_DIR. Source it AFTER .env and BEFORE any lease/agent_status/
# matcher call. Idempotent; safe to source once per boot.
#
#   VENV_PY="$ORCH_DIR/.venv/bin/python3"
#   source "$ORCH_DIR/scripts/lib/pin_fleet_host_id.sh"
#
# Nazim add A: an unpinned host is LOUD ('fragile fallback'), never silent — it still boots
# (rides the resolver's tier-2 alias-match / tier-3 fallback), but visibly.
# Nazim add B: the pin MUST match a host the substrate already knows (agent_status.host /
# lease holder_host); a pin absent from known hosts is a fleet_hosts.json misconfig that
# would silently mis-scope host-matching fleet-wide -> FAIL LOUD + exit 1 (refuse to boot).

_FHID_VENV="${VENV_PY:-$ORCH_DIR/.venv/bin/python3}"
if _FHID_LABEL="$("$_FHID_VENV" "$ORCH_DIR/scripts/lib/fleet_host_id.py" resolve 2>/dev/null)"; then
    export FLEET_HOST_ID="$_FHID_LABEL"
    echo "▶ FLEET_HOST_ID pinned = $FLEET_HOST_ID (stable host identity; flap-proof)"
    if ! "$_FHID_VENV" - "$FLEET_HOST_ID" <<PY
import os, sys, psycopg
sys.path.insert(0, os.path.join("$ORCH_DIR", "scripts", "lib"))
import fleet_host_id
pin = sys.argv[1]
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn, connect_timeout=8) as c:
    sys.exit(0 if fleet_host_id.is_known_host(pin, c) else 1)
PY
    then
        echo "❌ FLEET_HOST_ID='$FLEET_HOST_ID' matches NO agent_status.host or lease holder_host — likely a fleet_hosts.json misconfig for this box. Refusing to boot under a possibly mis-scoped identity (Nazim add B)." >&2
        # NOTE (Nazim non-blocking #1): a genuinely NEW, correctly-mapped host has no prior
        # row yet (chicken-and-egg). For the 3 current hosts this always passes; the future
        # host-provisioning flow seeds the row (or lets first-boot pass on map-membership).
        exit 1
    fi
else
    echo "⚠️  FLEET_HOST_ID NOT pinned — this host is not in $ORCH_DIR/scripts/lib/fleet_hosts.json; running on the FRAGILE hostname fallback (flap-prone). Add this host to the map + redeploy." >&2
fi
