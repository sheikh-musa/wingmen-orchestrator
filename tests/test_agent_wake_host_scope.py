"""Tests for the HOST-SCOPE partition of the realtime wake doorbell.

When >1 host runs an agent_wake_subscriber (today the Mac Mini AND gzb), each must
wake ONLY the recipients registered to ITS OWN host, so the two never both act on a
single bus row. The decision is the pure function agent_wake.host_scope_allows; the
realtime wiring (agent_messages_realtime._host_scope_allows_wake) applies it after
looking up the recipient's registered host. Both are covered here with NO DB / tmux.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system import agent_wake
from nervous_system import agent_messages_realtime as rt


# ── pure partition rule ───────────────────────────────────────────────────────
@pytest.mark.parametrize("recipient_host,host_scope,expected", [
    # KNOWN host, matching scope -> this subscriber owns the wake.
    ("gzbai", "gzbai", True),
    ("Sheikhs-Mini", "Sheikhs-Mini", True),
    # KNOWN host, NON-matching scope -> the OTHER subscriber owns it; skip (no double-wake).
    ("Sheikhs-Mini", "gzbai", False),
    ("gzbai", "Sheikhs-Mini", False),
    # UNSCOPED subscriber (host_scope falsy) -> legacy behavior: always allow.
    ("gzbai", None, True),
    ("Sheikhs-Mini", "", True),
    (None, None, True),
    # UNKNOWN recipient host (no agent_status row) -> allow; local-tmux gate decides.
    (None, "gzbai", True),
    ("", "Sheikhs-Mini", True),
])
def test_host_scope_allows(recipient_host, host_scope, expected):
    assert agent_wake.host_scope_allows(recipient_host, host_scope) is expected


def test_partition_is_mutually_exclusive_for_a_known_lane():
    """The core invariant: for any lane with a KNOWN host, exactly ONE of the two
    subscribers' scopes allows the wake — never both, never neither."""
    for lane_host in ("gzbai", "Sheikhs-Mini"):
        mini = agent_wake.host_scope_allows(lane_host, "Sheikhs-Mini")
        gzb = agent_wake.host_scope_allows(lane_host, "gzbai")
        assert (mini, gzb).count(True) == 1


# ── realtime wiring: _host_scope_allows_wake ──────────────────────────────────
class TestRealtimeHostScopeGate:
    async def test_unscoped_short_circuits_without_db(self):
        """host_scope=None must NOT query the DB (legacy single-subscriber path)."""
        with patch.object(rt.agent_wake, "agent_registered_host") as lookup:
            allow = await rt._host_scope_allows_wake("cc-irsyad-coord", None, 1)
        assert allow is True
        lookup.assert_not_called()

    async def test_matching_host_allows(self):
        with patch.object(rt.agent_wake, "agent_registered_host", return_value="gzbai"):
            allow = await rt._host_scope_allows_wake("cc-irsyad-coord", "gzbai", 2)
        assert allow is True

    async def test_foreign_host_skips(self):
        """A gzb-registered lane is NOT woken by the Mini-scoped subscriber."""
        with patch.object(rt.agent_wake, "agent_registered_host", return_value="gzbai"):
            allow = await rt._host_scope_allows_wake("cc-irsyad-coord", "Sheikhs-Mini", 3)
        assert allow is False

    async def test_unknown_host_falls_back_to_local_gate(self):
        with patch.object(rt.agent_wake, "agent_registered_host", return_value=None):
            allow = await rt._host_scope_allows_wake("cc-brand-new", "gzbai", 4)
        assert allow is True


# ── end-to-end: the wake is skipped for a foreign-host recipient ──────────────
def _supabase_with_row(row):
    from unittest.mock import MagicMock
    supabase = MagicMock()
    execute = AsyncMock(return_value=MagicMock(data=[row] if row else []))
    supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = execute
    return supabase


class TestRouteSingleMessageHostScope:
    _MSG = {
        "id": 55, "from_agent": "cc-orchestrator", "to_agent": "cc-irsyad-coord",
        "message_type": "blocker", "subject": "x", "body": "x",
        "requires_response": True, "priority": "P1", "created_at": "2026-10-02T00:00:00Z",
        "is_test": False, "forwarded_to_telegram_at": None, "skipped_at": None, "read_at": None,
    }

    async def test_foreign_host_recipient_not_woken(self):
        """A gzb lane routed through the MINI-scoped subscriber -> wake_agent NEVER called."""
        supabase = _supabase_with_row(self._MSG)
        with patch.object(rt.agent_wake, "auto_wake_enabled", return_value=True), \
             patch.object(rt.agent_wake, "agent_registered_host", return_value="gzbai"), \
             patch.object(rt.agent_wake, "wake_agent") as wake:
            await rt._route_single_message(supabase, None, None, 55,
                                           wake_only=True, host_scope="Sheikhs-Mini")
        wake.assert_not_called()

    async def test_matching_host_recipient_woken(self):
        """Same lane through the GZB-scoped subscriber -> wake_agent IS called."""
        supabase = _supabase_with_row(self._MSG)
        with patch.object(rt.agent_wake, "auto_wake_enabled", return_value=True), \
             patch.object(rt.agent_wake, "agent_registered_host", return_value="gzbai"), \
             patch.object(rt.agent_wake, "wake_agent", return_value={"woke": True}) as wake:
            await rt._route_single_message(supabase, None, None, 55,
                                           wake_only=True, host_scope="gzbai")
        wake.assert_called_once()
