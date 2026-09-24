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

UPDATE (op#42896/#42909, 2026-09-24): the gzb reach text ITSELF went dead when op#42907
deleted the gzb-vpn.sh/wingmen-core relay it described, and no replacement interactive
SSH path to gzb was built. `hub_reach_for_holder("gzbai")` now says so honestly (reach=None,
remedy names the gap + gzb's systemd self-supervision) rather than describing a hop that no
longer exists — same fail-safe posture as the unknown-holder branch, applied to a KNOWN
host whose ONE known route died.
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
        # op#42907/op#20655 (2026-09-24): the old relay this remedy told a responder to
        # use -- ssh hub-vps -> sudo gzb-vpn.sh up -> sudo -u wingmen -H ssh gzb -- is
        # DEAD. gzb-vpn.sh and the wingmen-core hop were deleted when the backup path was
        # repointed to gzb's Tailscale IP directly, and wingmen-core itself is cleared for
        # power-off. No replacement interactive/shell SSH reach to gzb has been built —
        # the only gzb credential in this repo (scripts/daily_backup.sh's `wbackup`) is a
        # push/prune-only backup account with no shell, unusable for a tmux nudge/reset.
        # Per this module's own fail-safe rule (never point at a route we can't actually
        # use), reach/remedy are honest about the gap rather than describing a dead hop.
        reach = None
        remedy = ("REMEDY: the hub is on gzb (Tailscale 100.77.251.8), but no automated "
                  "interactive SSH reach to it is provisioned in this repo -- the old "
                  "wingmen-core relay (gzb-vpn.sh) was decommissioned (op#42907/op#20655) "
                  "and never replaced. gzb's hub process is systemd-supervised "
                  "(wingmen-orch-hub.service) so a DEAD process self-restarts, but a WEDGED-"
                  "but-alive composer needs a human with real shell access on gzb directly -- "
                  "escalate to the operator rather than attempt reset_hub_remote.sh (it "
                  "correctly refuses for a gzb holder) or invent an untested reach path.")
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
