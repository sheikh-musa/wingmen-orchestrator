"""Tests for the 5-hour-window threshold page in weekly_limit_monitor.

Context (op#14438 consolidation, cc-fleet-health): the monitor's threshold pages
key on the WEEKLY (u7d) gauge only. The 5-HOUR burst window — the constraint that
actually stalls a whole pool during a max-velocity push (it stalled the irsyad
fleet at 76% on 2026-08-17 while the weekly gauge still looked comfortable) — had
NO threshold page. These tests pin the pure 5h-window transition helper that closes
that fail-loud gap: it fires at most once per 5h window per level, keyed on reset5h,
mirroring the weekly dedup, and never fires on a missing/ok reading.
"""

from nervous_system.weekly_limit_monitor import _fivehr_transition, WARN, URGENT


class TestFivehrTransition:
    def test_none_reading_never_pages(self):
        pkey = {}
        assert _fivehr_transition(pkey, None, 111.0) is None

    def test_below_warn_is_quiet_and_clears_memory(self):
        pkey = {"alerted_5h": "warn", "reset5h": 111.0}
        # same window, now back under WARN -> no page, memory cleared
        assert _fivehr_transition(pkey, WARN - 0.01, 111.0) is None
        assert pkey["alerted_5h"] is None

    def test_fresh_warn_crossing_pages_once(self):
        pkey = {}
        assert _fivehr_transition(pkey, WARN + 0.01, 111.0) == "warn"
        # same window, still warn -> deduped
        assert _fivehr_transition(pkey, WARN + 0.02, 111.0) is None

    def test_warn_escalates_to_urgent_same_window(self):
        pkey = {}
        assert _fivehr_transition(pkey, WARN + 0.01, 111.0) == "warn"
        assert _fivehr_transition(pkey, URGENT + 0.01, 111.0) == "urgent"
        # already urgent this window -> deduped
        assert _fivehr_transition(pkey, URGENT + 0.02, 111.0) is None

    def test_new_5h_window_re_arms(self):
        pkey = {}
        assert _fivehr_transition(pkey, WARN + 0.01, 111.0) == "warn"
        # reset5h advanced => fresh window => pages again
        assert _fivehr_transition(pkey, WARN + 0.01, 222.0) == "warn"

    def test_urgent_from_cold_pages_urgent(self):
        pkey = {}
        assert _fivehr_transition(pkey, URGENT + 0.05, 111.0) == "urgent"

    def test_5h_memory_is_independent_of_weekly(self):
        # a pkey already carrying weekly state must not confuse the 5h path
        pkey = {"alerted": "urgent", "reset": 999.0, "u7d": 0.99}
        assert _fivehr_transition(pkey, WARN + 0.01, 111.0) == "warn"
        assert pkey["alerted_5h"] == "warn"
        # weekly keys untouched
        assert pkey["alerted"] == "urgent"
