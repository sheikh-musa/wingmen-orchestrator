"""fleet_host_id — the ONE stable host-identity source for the fleet.

WHY: host identity used to come straight off live `socket.gethostname()`, which
flaps when a DHCP/DNS-derived hostname changes (the 2026-09-20 Sheikhs-Mini ->
Sheikhs-Mac-mini incident, bus 41834). That single live call is the key for BOTH
the host-scoped lane-wedge watchdog matching AND the lease dead-man's switch, so
one flap false-gapped a healthy lane and near-false-failed-over the SRE lease.

This consolidates every host-identity read onto one deterministic, flap-proof
resolver (CAI-RESP-1436 identity gate + Nazim floor A-D):

  1. FLEET_HOST_ID env  — the DURABLE path, boot-exported from the git-tracked
     fleet_hosts map. Prod hosts MUST export this. Silent when present.
  2. alias-match         — the live hostname matched against the git-tracked
     fleet_hosts map's known aliases -> canonical label. A RESILIENCE net for a
     known flap mid-rollout; it is OBSERVABLE (logs 'host not pinned') so it can
     never silently become the permanent state instead of a real pin.
  3. raw fallback        — gethostname().split('.')[0] (today's behavior). LOUD
     warn ('fragile hostname fallback') so an un-mapped host never rides quietly.

A gethostname() failure PROPAGATES (not swallowed) so consumers apply their own
explicit error policy (Nazim add D): the watchdog matchers fail toward SURFACING
(a false gap alert is safe; a silent miss is not); the lease take/renew fail
CLOSED (never act under an unknown identity).
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

ENV_KEY = "FLEET_HOST_ID"
MAP_PATH = Path(__file__).with_name("fleet_hosts.json")


def _log(msg: str) -> None:
    """Observability sink (monkeypatched in tests). Best-effort stderr — must
    never itself raise or block identity resolution."""
    try:
        print(f"[fleet_host_id] {msg}", file=sys.stderr)
    except Exception:
        pass


def _load_map() -> dict:
    """canonical_label -> set-of-known-aliases (incl. the canonical itself).

    ROBUST: an unreadable/corrupt map degrades to {} (-> loud raw fallback),
    never a crash — identity resolution must survive a bad config file."""
    try:
        raw = json.loads(MAP_PATH.read_text())
        hosts = raw.get("hosts", raw) if isinstance(raw, dict) else {}
        out: dict = {}
        for canon, v in hosts.items():
            if canon.startswith("_"):
                continue  # comment keys
            aliases = v.get("aliases", []) if isinstance(v, dict) else list(v)
            out[canon] = sorted({canon, *aliases})
        return out
    except Exception:
        return {}


def _match_alias(name: str, mapping: dict | None = None) -> "str | None":
    """Return the canonical label whose alias set contains `name` (or its
    short, .local-stripped form), else None."""
    if not name:
        return None
    if mapping is None:
        mapping = _load_map()
    cands = {name, name.split(".")[0]}
    for canon, aliases in mapping.items():
        if cands & set(aliases):
            return canon
    return None


def fleet_host_id() -> str:
    """The stable host identity. See module docstring for the tiers. May raise
    (only) if the host has no pin, no alias-match, AND socket.gethostname()
    itself fails — callers handle per their error policy (Nazim add D)."""
    # 1) durable pin — boot-exported from the reviewed map.
    pin = os.environ.get(ENV_KEY)
    if pin and pin.strip():
        return pin.strip()
    # 2) alias-match — resilience net; OBSERVABLE so an unpinned host is visible.
    live = socket.gethostname()  # may raise OSError -> propagate to caller.
    canon = _match_alias(live, _load_map())
    if canon:
        _log(f"host not pinned (FLEET_HOST_ID unset) — resolved '{live}' -> "
             f"'{canon}' via alias-match; export FLEET_HOST_ID={canon} to pin it")
        return canon
    # 3) loud raw fallback — today's flappy behavior, never silent.
    short = live.split(".")[0]
    _log(f"WARNING: running on fragile hostname fallback — no FLEET_HOST_ID pin "
         f"and '{live}' is not in the fleet_hosts map; using '{short}' (flap-prone)")
    return short


def resolve_pin() -> "tuple[str, bool]":
    """Boot helper: compute the canonical label to EXPORT as FLEET_HOST_ID, from the
    live hostname via the git-tracked map (ignoring any existing env pin). Returns
    (label, mapped): mapped=True when the map covered this host (safe to pin durably);
    mapped=False means the host is NOT in fleet_hosts.json -> boot must WARN and the
    runtime rides the fragile fallback until it's added (Nazim add A / cai deploy-to-all)."""
    live = socket.gethostname()
    canon = _match_alias(live, _load_map())
    if canon:
        return canon, True
    return live.split(".")[0], False


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Stable fleet host identity (CAI-RESP-1436).")
    ap.add_argument("cmd", choices=["current", "resolve"],
                    help="current = full resolution incl. env pin; resolve = boot helper "
                         "(canonical from the map, exit 3 + WARN if this host is unmapped)")
    args = ap.parse_args(argv)
    if args.cmd == "current":
        print(fleet_host_id())
        return 0
    label, mapped = resolve_pin()
    print(label)
    if not mapped:
        _log(f"WARNING: '{socket.gethostname()}' is not in fleet_hosts.json — cannot pin "
             f"FLEET_HOST_ID durably; add this host to the map + redeploy (fragile fallback until then)")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
