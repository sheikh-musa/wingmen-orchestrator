"""Tests for the address-variant-miss detector (bus #51166 regression class).

Signature: a lane has unread mail addressed to its INSTANCE id, old enough to
not be a landing-race, while it has read mail on its BASE id recently enough
to prove it is awake and draining its inbox.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.address_variant_miss_watchdog import (
    due_for_page,
    find_address_variant_misses,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _row(instance_id, base_id, unread_age_min, base_read_age_min, unread_count=1):
    return {
        "instance_id": instance_id,
        "base_agent_id": base_id,
        "oldest_unread_created_at": NOW - timedelta(minutes=unread_age_min) if unread_age_min is not None else None,
        "unread_count": unread_count,
        "last_base_read_at": NOW - timedelta(minutes=base_read_age_min) if base_read_age_min is not None else None,
    }


def test_flags_the_precise_signature():
    rows = [_row("cc-shipforge-1", "cc-shipforge", unread_age_min=30, base_read_age_min=5)]
    misses = find_address_variant_misses(rows, now=NOW)
    assert len(misses) == 1
    assert misses[0]["instance_id"] == "cc-shipforge-1"


def test_ignores_fresh_unread_landing_race():
    # Unread only 5 min old: give the lane a chance to read it normally first.
    rows = [_row("cc-shipforge-1", "cc-shipforge", unread_age_min=5, base_read_age_min=2)]
    assert find_address_variant_misses(rows, now=NOW) == []


def test_ignores_lane_with_no_recent_base_read():
    # Old unread but base read is stale too (or never) -> generically idle/dead,
    # a different failure class already covered elsewhere.
    rows = [_row("cc-shipforge-1", "cc-shipforge", unread_age_min=60, base_read_age_min=600)]
    assert find_address_variant_misses(rows, now=NOW) == []


def test_ignores_lane_that_never_read_base():
    rows = [_row("cc-shipforge-1", "cc-shipforge", unread_age_min=60, base_read_age_min=None)]
    assert find_address_variant_misses(rows, now=NOW) == []


def test_ignores_lane_with_no_unread():
    rows = [_row("cc-shipforge-1", "cc-shipforge", unread_age_min=None, base_read_age_min=5)]
    assert find_address_variant_misses(rows, now=NOW) == []


def test_due_for_page_never_paged_before():
    assert due_for_page("cc-shipforge-1", {}, now_ts=1000.0) is True


def test_due_for_page_within_cooldown():
    state = {"cc-shipforge-1": {"last_paged_ts": 1000.0}}
    assert due_for_page("cc-shipforge-1", state, now_ts=1000.0 + 60, cooldown_min=240) is False


def test_due_for_page_after_cooldown():
    state = {"cc-shipforge-1": {"last_paged_ts": 1000.0}}
    assert due_for_page("cc-shipforge-1", state, now_ts=1000.0 + 241 * 60, cooldown_min=240) is True
