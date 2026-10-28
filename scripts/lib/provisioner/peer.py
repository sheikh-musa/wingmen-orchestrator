"""peer.py — the AF_UNIX caller identity, cross-platform.

exec_broker's acl.peer_uid is SO_PEERCRED, which is LINUX-ONLY. This
provisioner daemon runs on the Mac Mini, so the macOS path is LOCAL_PEERCRED
— but BOTH details commonly published about it are wrong, and both were
falsified by C-level probes on this Mini (Darwin 24.6, 2026-10-28):

  1. The option value is 0x001, not 0x002 (sys/un.h: LOCAL_PEERCRED=0x001;
     0x002 is LOCAL_PEERPID).
  2. The level is SOL_LOCAL (0), NOT SOL_SOCKET — at SOL_SOCKET the value
     0x001 is shadowed by SO_DEBUG and getsockopt happily returns 4 zero
     bytes, i.e. uid 0. A wrong-level implementation would read uid=0 for
     every peer and, against a root-including allowlist, admit anyone.

The verified working call (C probe, same-machine): getsockopt(SOL_LOCAL,
LOCAL_PEERCRED, 76) -> struct xucred {u32 cr_version=0; u32 cr_uid; ...},
len 76. peer_uid() tries SO_PEERCRED (Linux) first, falls back to
SOL_LOCAL/LOCAL_PEERCRED (macOS), and raises if neither works — the server
maps any failure to fail-closed INTERNAL_ERROR, never "allow". Never trust
the request body's self-reported caller field for authorization; it is
logged, not trusted.
"""
from __future__ import annotations

import socket
import struct

# Darwin sys/un.h; not in Python's socket module on any platform.
SOL_LOCAL = 0
LOCAL_PEERCRED = 0x001

# sizeof(struct xucred) per the C probe; cr_version then cr_uid as two u32s.
_XUCRED_FETCH_BYTES = 76


def _unpack_xucred(buf: bytes) -> int:
    _version, uid = struct.unpack_from("2I", buf, 0)
    return uid


def peer_uid(conn: socket.socket) -> int:
    """Return the connecting peer's unix UID. SO_PEERCRED (Linux) first,
    SOL_LOCAL/LOCAL_PEERCRED (macOS) as fallback; raises OSError when
    neither mechanism works — fail-closed."""
    if hasattr(socket, "SO_PEERCRED"):
        creds = conn.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        _pid, uid, _gid = struct.unpack("3i", creds)
        return uid
    creds = conn.getsockopt(SOL_LOCAL, LOCAL_PEERCRED, _XUCRED_FETCH_BYTES)
    return _unpack_xucred(creds)
