#!/usr/bin/env python3
"""acl.py — SO_PEERCRED-based ACL (contract: "ACL is SO_PEERCRED-based on the
ACTUAL connecting unix UID — an explicit allowlist. Never trust the request
body's self-reported caller field for authorization; it's logged, not
trusted.")

Linux-only (SO_PEERCRED). On an unsupported platform, peer_uid() raises
AttributeError/OSError — server.py's caller maps that to INTERNAL_ERROR
(fail-closed, never "allow").
"""
from __future__ import annotations

import socket
import struct

_SO_PEERCRED_STRUCT = "3i"  # pid_t, uid_t, gid_t (Linux)


class AclDenied(Exception):
    """The connecting unix UID is not on the broker's allowlist."""


def peer_uid(conn: socket.socket) -> int:
    """Return the connecting peer's unix UID via SO_PEERCRED."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize(_SO_PEERCRED_STRUCT))
    _pid, uid, _gid = struct.unpack(_SO_PEERCRED_STRUCT, creds)
    return uid


def check_acl(uid: int, allowed_uids: frozenset[int]) -> None:
    """Raise AclDenied if uid is not on the explicit allowlist."""
    if uid not in allowed_uids:
        raise AclDenied(f"uid {uid} is not on the broker's allowlist")
