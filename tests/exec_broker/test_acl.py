"""hc#95 exec-broker SO_PEERCRED ACL."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.acl import AclDenied, check_acl, peer_uid  # noqa: E402


def test_check_acl_allows_listed_uid():
    check_acl(1000, frozenset({1000, 1001}))  # no raise


def test_check_acl_denies_unlisted_uid():
    with pytest.raises(AclDenied):
        check_acl(2000, frozenset({1000, 1001}))


def test_check_acl_denies_empty_allowlist():
    with pytest.raises(AclDenied):
        check_acl(1000, frozenset())


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED is Linux-only")
def test_peer_uid_over_real_socketpair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert peer_uid(a) == os.getuid()
        assert peer_uid(b) == os.getuid()
    finally:
        a.close()
        b.close()
