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
deleted the gzb-vpn.sh/wingmen-core relay it described; for a while `hub_reach_for_holder("gzbai")`
honestly reported reach=None (the gap) rather than a dead hop.

UPDATE (Nazim 43610/43612, 2026-09-27): a dedicated fleet-ops key + ssh-config alias `gzb`
(gazzai@100.77.251.8 Tailscale-direct, ~/.ssh/gzb_fleet) RESTORED a read/probe + nudge reach
to the gzb hub, so the gzbai branch now returns a real reach again. The dict also exposes
`ssh_target` (a runnable ssh destination: "gzb" for gzbai, "root@91.107.235.77" for
wingmen-core, None when unknown) so callers (console panes, reset paths) resolve the host path
HERE instead of re-hardcoding it. A hard reset still needs root (the key has no sudo) → that
step stays operator/vault-gated.
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
        # 2026-09-27 (Nazim 43610/43612): a dedicated fleet-ops key RESTORED a READ/probe
        # reach to the gzb hub -- ssh-config alias `gzb` (gazzai@100.77.251.8 Tailscale-direct,
        # IdentityFile ~/.ssh/gzb_fleet, IdentitiesOnly). The orch tmux session is gazzai-owned,
        # so capture-pane needs NO sudo. This replaces the honest "no reach provisioned" gap
        # left when op#42907/op#20655 deleted the old wingmen-core/gzb-vpn.sh relay. WAKING the
        # hub is NOT done by raw send-keys here (Nazim 43617): a raw `tmux send-keys` into a lane
        # bypasses the menu-guard/ghost-probe choke point and is forbidden by fleet doctrine --
        # and this remedy string is exactly what gets copy-pasted under pressure. Wake via the
        # bus wake floor instead. A HARD reset still needs root the fleet key does NOT grant.
        reach = ("ssh gzb (ssh-config alias -> gazzai@100.77.251.8 Tailscale-direct, "
                 "key ~/.ssh/gzb_fleet) -> tmux 'orch' (gazzai-owned), READ-only")
        remedy = ("REMEDY: READ the hub via `ssh gzb tmux capture-pane -t orch -p` (safe, "
                  "read-only). To WAKE an idle-but-alive hub, post a P1 + requires_response bus "
                  "row to cc-orchestrator (the hub's wake floor), or use the sanctioned guarded "
                  "nudge path if it supports the gzb target -- NEVER bypass that guard by "
                  "injecting raw keystrokes into the lane (that skips the menu-guard/ghost-probe "
                  "choke point, which fleet doctrine forbids). A HARD reset (systemctl restart "
                  "wingmen-orch-hub.service) needs root and the gzb_fleet key has NO sudo -- "
                  "escalate that step to the operator/console with the vault password. Do NOT "
                  "use reset_hub_remote.sh (it targets the decommissioned wingmen-core and "
                  "correctly refuses a gzb holder).")
        return {"known": True, "host": "gzbai", "tmux": "orch", "reach": reach,
                "remedy": remedy, "ssh_target": "gzb"}
    if canon == "wingmen-core":
        reach = ("ssh root@91.107.235.77 (hub-vps) -> tmux 'orch' (user wingmen)")
        remedy = (f"REMEDY: reach the hub directly on the VPS — {reach}; forced typed-nudge / "
                  f"reset_orch.sh runs there. Console pen.")
        return {"known": True, "host": "wingmen-core", "tmux": "orch", "reach": reach,
                "remedy": remedy, "ssh_target": "root@91.107.235.77"}
    # unknown / unset / unrecognized: fail-safe — name NO host.
    remedy = ("REMEDY: the hub's holder_host is unknown/unset — resolve it from "
              "orch_lease.holder_host before reaching; do NOT assume a host (a stale one "
              "may be decommissioned).")
    return {"known": False, "host": None, "tmux": "orch", "reach": None,
            "remedy": remedy, "ssh_target": None}


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
