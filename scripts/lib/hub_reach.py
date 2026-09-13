"""hub_reach — resolve the hub's reach path + remedy text from orch_lease.holder_host.

Root cause (Nazim 39292/39435, 2026-09-12): hub remedy text across the watchdogs
(singleton_liveness.page_wedged_alive, context_health_watchdog external_recycle,
reset_hub_remote.sh) HARDCODED the decommissioned wingmen-core host (91.107.235.77)
while the live hub is on gzbai. A responder pointed there hits a dead box, so
auto-recovery is a no-op. Every hub remedy/page must instead resolve the CURRENT
host from orch_lease.holder_host — and when it cannot, name NO host rather than a
stale one (fail-safe, never a guess).

PURE core: `hub_reach_for_holder(holder_host)` + `is_reach_host_current(...)`.
Thin DB reader: `read_holder_host(conn)`.
"""
from __future__ import annotations

# Canonical host groups. holder_host in orch_lease is the authority; these aliases
# map the various spellings/IPs to one canonical key so callers never string-compare
# a raw host. Extend here (one place) if the hub relocates again.
_GZB_ALIASES = {"gzbai", "gzb", "192.168.1.114"}
_VPS_ALIASES = {"wingmen-core", "hermes-vps", "91.107.235.77"}


def _canon(holder_host: "str | None") -> "str | None":
    """Canonical group key ('gzbai' | 'wingmen-core') or None if unknown/unset.
    None is NEVER coerced to a guessed host — unknown stays unknown (fail-safe)."""
    if not holder_host:
        return None
    h = holder_host.strip().lower()
    if h in _GZB_ALIASES:
        return "gzbai"
    if h in _VPS_ALIASES:
        return "wingmen-core"
    return None


def hub_reach_for_holder(holder_host: "str | None") -> dict:
    """PURE. Given orch_lease.holder_host, return the hub's reach descriptor:
      {known, host, tmux, reach, remedy}
    - known=True + a host-correct reach/remedy for a recognized holder;
    - known=False + a SAFE remedy that names NO host (says "resolve from orch_lease")
      for an unknown/unset/unrecognized holder — never a stale-host guess.
    """
    canon = _canon(holder_host)
    if canon == "gzbai":
        reach = ("ssh hub-vps -> sudo -n gzb-vpn.sh up (check status; connect can take "
                 "up to ~2min) -> sudo -u wingmen -H ssh gzb -> tmux 'orch'")
        remedy = (f"REMEDY: reach the hub via the gzb split-tunnel — {reach}. The hub is on "
                  f"the gzb LAN (192.168.1.114); a forced typed-nudge (C-u C-u + literal drain "
                  f"+ Enter) submits its staged/ghost composer. This is the console pen (cross-"
                  f"host mutating action). Do NOT target the old decommissioned VPS.")
        return {"known": True, "host": "gzbai", "tmux": "orch", "reach": reach, "remedy": remedy}
    if canon == "wingmen-core":
        reach = ("ssh root@91.107.235.77 (hub-vps) -> tmux 'orch' (user wingmen)")
        remedy = (f"REMEDY: reach the hub directly on the VPS — {reach}; forced typed-nudge / "
                  f"reset_orch.sh runs there. Console pen.")
        return {"known": True, "host": "wingmen-core", "tmux": "orch", "reach": reach, "remedy": remedy}
    # unknown / unset / unrecognized: fail-safe — name NO host.
    remedy = ("REMEDY: the hub's holder_host is unknown/unset — resolve it from "
              "orch_lease.holder_host before reaching; do NOT assume a host (a stale one "
              "may be decommissioned).")
    return {"known": False, "host": None, "tmux": "orch", "reach": None, "remedy": remedy}


def is_reach_host_current(target_host: "str | None", holder_host: "str | None") -> bool:
    """PURE guard for a MUTATING reach (e.g. reset_hub_remote.sh): may we act on
    `target_host`? True ONLY when target_host and holder_host resolve to the SAME
    recognized group. Fail-CLOSED on any unknown/unset holder — never reset a host
    we cannot confirm is the current lease holder (the decommissioned-box guard)."""
    ct = _canon(target_host)
    ch = _canon(holder_host)
    return ct is not None and ch is not None and ct == ch


def read_holder_host(conn) -> "str | None":
    """Thin reader: current orch_lease.holder_host, or None. Caller owns the conn.
    Fail-safe: any read miss -> None (unknown), which the callers treat as fail-closed."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT holder_host FROM orch_lease WHERE lease_key='orch-hub'")
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
