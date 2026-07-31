"""Operator access control: Tailscale-IP allowlist + dormant breakglass token.

Replaces the CONSOLE_TOKEN static-password gate (CAI-RESP-264 condition 1,
"network/identity layer IN FRONT of the token") with the IP-allowlist option
from that binding hardening list.

- Primary gate: the REAL TCP peer address must be in CONSOLE_ALLOWED_IPS (a
  comma-separated list of Tailscale IPs). The peer address MUST come from the
  socket (`self.client_address[0]` at the call site in app.py) — NEVER from a
  client-supplied header like X-Forwarded-For. XFF is trivially spoofable by
  anyone who can reach this process at all, so trusting it would make an
  IP-allowlist *worse* than the password it replaces (a bypass any client can
  set, versus a secret that at least required possession).
- Fail-CLOSED: an empty/unset allowlist rejects every request. It must never
  mean "open" — a misconfiguration should lock everyone out, not no one.
- CONSOLE_BREAKGLASS_TOKEN is DORMANT: empty/unset by default, and only ever
  consulted for a peer IP that already failed the allowlist check. It exists
  solely so a Tailscale re-registration event (a device's tailnet IP changing
  — the exact failure mode that already locked the operator's phone out of
  the Mini once) can't turn into a total, un-recoverable lockout. It is
  header-only (`Authorization: Bearer`), same anti-URL-leak rule as the token
  it replaces, and every use is logged loudly and separately from normal
  per-request access audit — it should fire rarely, if ever.

Stdlib-only.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Mapping, Set, Tuple

logger = logging.getLogger("wingmen.console.auth")

_DEFAULT_LOG = (
    pathlib.Path(__file__).resolve().parents[2] / "logs" / "console_access.log"
)


def _allowed_ips() -> Set[str]:
    raw = os.environ.get("CONSOLE_ALLOWED_IPS", "") or ""
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def _peer_allowed(peer_ip: str, allowed: Set[str]) -> bool:
    """True iff *peer_ip* matches an allowlist entry.

    An entry is either an exact address (fast path, original behaviour) or a
    CIDR network like ``100.64.0.0/10`` (the Tailscale CGNAT range → "any
    device on the tailnet"). A malformed entry or peer IP never raises — it is
    treated as no-match, preserving the fail-closed contract. Headers/XFF are
    still never consulted; *peer_ip* is the real TCP peer from the socket.
    """
    if not peer_ip:
        return False
    if peer_ip in allowed:  # exact match — unchanged fast path
        return True
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    for entry in allowed:
        if "/" not in entry:
            continue
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue  # a bad CIDR entry must not admit anyone, nor crash
        if addr.version == net.version and addr in net:
            return True
    return False


def _breakglass_token() -> str:
    return os.environ.get("CONSOLE_BREAKGLASS_TOKEN", "") or ""


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return ""


def check_access(peer_ip: str, headers: Mapping[str, str]) -> Tuple[bool, str]:
    """Return (authorized, method).

    method is one of:
      "ip"          - peer_ip matched CONSOLE_ALLOWED_IPS (the normal path)
      "breakglass"  - the dormant recovery token matched (should be rare)
      "denied"      - rejected

    *peer_ip* MUST be the real TCP peer address — never a client-controlled
    header. This function never reads X-Forwarded-For or any other header to
    determine the caller's IP; headers are consulted ONLY for the breakglass
    Authorization header.
    """
    allowed = _allowed_ips()
    if not allowed:
        return False, "denied"  # fail-closed: empty allowlist locks EVERYONE out

    if _peer_allowed(peer_ip, allowed):
        return True, "ip"

    breakglass = _breakglass_token()
    if breakglass:
        raw = _header(headers, "Authorization").strip()
        prefix = "Bearer "
        if raw.startswith(prefix):
            presented = raw[len(prefix):].strip()
            if presented and hmac.compare_digest(presented, breakglass):
                _log_breakglass(peer_ip)
                return True, "breakglass"

    return False, "denied"


def _log_breakglass(peer_ip: str) -> None:
    """Breakglass use means the allowlist itself failed someone — log it
    loudly (process log) in addition to whatever per-request audit line the
    caller writes regardless of method."""
    logger.warning(
        "CONSOLE BREAKGLASS TOKEN USED from %s — IP allowlist was bypassed", peer_ip
    )
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{ts}\t{peer_ip}\tBREAKGLASS\tused\n")
    except Exception:
        # Auditing failure must not take down the read-only surface.
        pass


def _log_path() -> pathlib.Path:
    override = os.environ.get("CONSOLE_ACCESS_LOG")
    return pathlib.Path(override) if override else _DEFAULT_LOG


def audit(client: str, path: str, outcome: str) -> None:
    """Append one access record. Never raises (auditing must not break serving)."""
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts}\t{client}\t{path}\t{outcome}\n"
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # Auditing failure must not take down the read-only surface.
        pass
