"""Tests for scripts/hub_self_recovery_staleness_watchdog.py — the pure staleness
classifier (hc#175, CAI-RESP-1439 pre-arming precondition).

Load-bearing: a stale (or NULL) last_tick_at must read 'stale' so the gzb timer going
silently dead is never mistaken for a healthy quiet period — unlike bus-currency
staleness, there is no 'quiet is ok' case here, because the gzb timer is SUPPOSED to
tick every ~2min regardless of whether the hub has pending work. No DB, no page.
"""
from scripts import hub_self_recovery_staleness_watchdog as m

MIN = 60.0


def test_fresh_tick_is_ok():
    v, _ = m.classify_tick_staleness(30.0, stale_s=480.0)
    assert v == "ok"


def test_tick_just_under_threshold_is_ok():
    v, _ = m.classify_tick_staleness(479.0, stale_s=480.0)
    assert v == "ok"


def test_tick_exactly_at_threshold_is_ok():
    # exactly at threshold is NOT > threshold — no false-fire on the boundary
    v, _ = m.classify_tick_staleness(480.0, stale_s=480.0)
    assert v == "ok"


def test_tick_past_threshold_is_stale():
    v, reason = m.classify_tick_staleness(9 * MIN, stale_s=480.0)
    assert v == "stale"
    assert "threshold" in reason


def test_long_dead_tick_is_stale():
    v, reason = m.classify_tick_staleness(3 * 3600.0, stale_s=480.0)
    assert v == "stale"


def test_null_last_tick_is_stale_never_a_free_pass():
    # a never-started/table-missing timer must NOT read as healthy-quiet
    v, reason = m.classify_tick_staleness(None, stale_s=480.0)
    assert v == "stale"
    assert "never" in reason.lower() or "null" in reason.lower()


def test_default_threshold_is_within_5_to_10_min_guidance():
    assert 5 * MIN <= m.STALE_S <= 10 * MIN
