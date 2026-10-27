#!/usr/bin/env python3
"""acl.py — SO_PEERCRED-based ACL (contract: "ACL is SO_PEERCRED-based on the
ACTUAL connecting unix UID — an explicit allowlist. Never trust the request
body's self-reported caller field for authorization; it's logged, not
trusted.")

Linux-only (SO_PEERCRED). On an unsupported platform, peer_uid() raises
AttributeError/OSError — server.py's caller maps that to INTERNAL_ERROR
(fail-closed, never "allow").

BOUNDED OVERRIDE (op#34322/#76985/#76989, 2026-10-26, orch-console gate):
check_acl optionally accepts a second, SEPARATE uid set with its own
expiry — a wall-clock-gated exception on top of the permanent allowlist,
never replacing it. Loaded once at daemon boot from env (same as the base
allowlist, see exec_broker_daemon.py), but re-checked against the current
time on EVERY call, so it reverts BY CONSTRUCTION the moment it expires —
no second restart needed to disarm, only to arm. A missing/None override is
simply inert (falls through to the permanent-allowlist-only behavior these
tests already proved), so this is purely additive.
"""
from __future__ import annotations

import socket
import struct
from datetime import datetime, timezone

_SO_PEERCRED_STRUCT = "3i"  # pid_t, uid_t, gid_t (Linux)


class AclDenied(Exception):
    """The connecting unix UID is not on the broker's allowlist."""


def peer_uid(conn: socket.socket) -> int:
    """Return the connecting peer's unix UID via SO_PEERCRED."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize(_SO_PEERCRED_STRUCT))
    _pid, uid, _gid = struct.unpack(_SO_PEERCRED_STRUCT, creds)
    return uid


def check_acl(
    uid: int,
    allowed_uids: frozenset[int],
    *,
    override_uids: "frozenset[int] | None" = None,
    override_expires_at: "datetime | None" = None,
    now: "datetime | None" = None,
) -> None:
    """Raise AclDenied if uid is not on the explicit allowlist, and not
    covered by a still-live bounded override.

    The override only ever WIDENS, never narrows, and only while
    `now < override_expires_at` — both override args must be set (and not
    expired) for it to grant anything; any missing/None/expired piece means
    the override is inert and only `allowed_uids` is consulted, matching the
    pre-override behavior exactly."""
    if uid in allowed_uids:
        return
    if (
        override_uids is not None
        and override_expires_at is not None
        and uid in override_uids
        and (now if now is not None else datetime.now(timezone.utc)) < override_expires_at
    ):
        return
    raise AclDenied(f"uid {uid} is not on the broker's allowlist")
