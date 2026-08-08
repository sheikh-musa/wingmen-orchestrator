"""Tests for nervous_system.agent_wake_subscriber — 5B heartbeat lag exposure.

The subscriber's heartbeat must carry the delivery-lag state (last delivered insert
id, DB max, lag) so an EXTERNAL dead-monitor (CAI-771 belt) can compare the exposed
last_insert_id against DB max(id) and ALERT LOUD even if the in-process exit(1) fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system import agent_wake_subscriber as sub
from nervous_system.agent_messages_realtime import _LivenessTracker


class TestHeartbeatPayload:
    def test_exposes_lag_fields(self):
        """A gap of 5 (db_max 1005, last delivered 1000) is exposed with a timestamp."""
        t = _LivenessTracker()
        t.seed(1000)
        t.evaluate(db_max=1005, now=0.0)  # opens a gap; db_max=1005, last delivered=1000
        payload = json.loads(sub._heartbeat_payload(t, now_epoch=1723100000.0))
        assert payload["ts"] == 1723100000.0
        assert payload["last_realtime_id"] == 1000
        assert payload["db_max"] == 1005
        assert payload["lag"] == 5

    def test_caught_up_reports_zero_lag(self):
        t = _LivenessTracker()
        t.seed(2000)
        t.note_delivered(2000)
        payload = json.loads(sub._heartbeat_payload(t, now_epoch=1723100000.0))
        assert payload["lag"] == 0
        assert payload["last_realtime_id"] == 2000
