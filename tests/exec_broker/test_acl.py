"""hc#95 exec-broker SO_PEERCRED ACL."""
from __future__ import annotations

import os
import socket
import sys
from datetime import datetime, timedelta, timezone
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


# --- bounded override (op#34322/#76985/#76989) -----------------------------

_NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_check_acl_override_allows_within_expiry():
    check_acl(
        2000,
        frozenset({1000}),
        override_uids=frozenset({2000}),
        override_expires_at=_NOW + timedelta(hours=1),
        now=_NOW,
    )  # no raise


def test_check_acl_override_denies_after_expiry():
    with pytest.raises(AclDenied):
        check_acl(
            2000,
            frozenset({1000}),
            override_uids=frozenset({2000}),
            override_expires_at=_NOW - timedelta(seconds=1),
            now=_NOW,
        )


def test_check_acl_override_denies_uid_not_in_override_set():
    with pytest.raises(AclDenied):
        check_acl(
            3000,
            frozenset({1000}),
            override_uids=frozenset({2000}),
            override_expires_at=_NOW + timedelta(hours=1),
            now=_NOW,
        )


def test_check_acl_override_inert_when_absent():
    # Exactly the pre-override call shape and behavior — no override args
    # given at all, matching every pre-existing caller/test.
    with pytest.raises(AclDenied):
        check_acl(2000, frozenset({1000}))


def test_check_acl_override_inert_when_only_uids_given_no_expiry():
    # Half an override (uids but no expiry) must not grant anything.
    with pytest.raises(AclDenied):
        check_acl(2000, frozenset({1000}), override_uids=frozenset({2000}), now=_NOW)


def test_check_acl_permanent_allowlist_still_wins_regardless_of_override():
    check_acl(
        1000,
        frozenset({1000}),
        override_uids=frozenset({9999}),
        override_expires_at=_NOW - timedelta(hours=1),  # already expired
        now=_NOW,
    )  # no raise -- 1000 was never dependent on the override


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED is Linux-only")
def test_peer_uid_over_real_socketpair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert peer_uid(a) == os.getuid()
        assert peer_uid(b) == os.getuid()
    finally:
        a.close()
        b.close()
