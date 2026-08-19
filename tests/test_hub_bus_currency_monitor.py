"""Tests for scripts/hub_bus_currency_monitor.py — the pure staleness classifier.

The load-bearing logic is classify_bus_currency: it must call the hub 'stale' ONLY when an
unread row has actually aged past the threshold (the drain-failure signal), and NEVER on an
empty/quiet inbox — keying on oldest-UNREAD age, not last-reconcile age, is what separates
'wedged' from 'quiet'. No DB, no page — pure function.
"""
import pytest

from scripts import hub_bus_currency_monitor as m

H = 3600.0  # seconds per hour


def test_empty_inbox_is_ok():
    v, _ = m.classify_bus_currency(0, None, stale_hours=2.5)
    assert v == "ok"


def test_zero_unread_with_stale_age_still_ok():
    # defensive: count<=0 dominates even if an age sneaks in
    v, _ = m.classify_bus_currency(0, 99 * H, stale_hours=2.5)
    assert v == "ok"


def test_none_oldest_age_is_ok():
    v, _ = m.classify_bus_currency(3, None, stale_hours=2.5)
    assert v == "ok"


def test_unread_within_threshold_is_ok():
    # 1h-old unread, 2.5h threshold — normal reconcile latency, not a stall
    v, reason = m.classify_bus_currency(2, 1 * H, stale_hours=2.5)
    assert v == "ok"
    assert "within" in reason


def test_unread_at_threshold_is_ok():
    # exactly at threshold is NOT > threshold — still ok (no false-fire on the boundary)
    v, _ = m.classify_bus_currency(1, 2.5 * H, stale_hours=2.5)
    assert v == "ok"


def test_unread_past_threshold_is_stale():
    # the 2026-08-19 case: unread aged well past threshold ⇒ bus undrained
    v, reason = m.classify_bus_currency(4, 11 * H, stale_hours=2.5)
    assert v == "stale"
    assert "undrained" in reason


def test_just_past_threshold_is_stale():
    v, _ = m.classify_bus_currency(1, 2.6 * H, stale_hours=2.5)
    assert v == "stale"


@pytest.mark.parametrize("hours,age_h,expected", [
    (3.0, 2.9, "ok"),
    (3.0, 3.1, "stale"),
    (1.0, 1.5, "stale"),
    (6.0, 5.0, "ok"),
])
def test_threshold_is_configurable(hours, age_h, expected):
    v, _ = m.classify_bus_currency(1, age_h * H, stale_hours=hours)
    assert v == expected
