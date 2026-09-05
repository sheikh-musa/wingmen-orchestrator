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


# ── session-deaf check (Nazim 37812): catch alive-host/dead-session — the gzb login-screen hole
#    where orch_lease + heartbeat both stayed FRESH via TIMERS while the claude SESSION was stuck
#    for 1.5h. Signal = hub last-real-turn age (NOT lease/heartbeat), gated on work-waiting so a
#    legitimately-quiet hub never false-pages (the quiet-vs-wedged concern). ──
M = 60.0  # seconds per minute


def test_session_deaf_when_host_up_silent_and_work_waiting():
    v, _ = m.classify_session_liveness(lease_fresh=True, last_activity_age_s=60 * M,
                                       work_waiting=True, threshold_s=45 * M)
    assert v == "session_deaf"


def test_quiet_hub_with_no_work_waiting_is_ok():
    # host up, no turn in an hour, but NOTHING waiting — a legitimately idle hub, not deaf. No page.
    v, _ = m.classify_session_liveness(lease_fresh=True, last_activity_age_s=60 * M,
                                       work_waiting=False, threshold_s=45 * M)
    assert v == "ok"


def test_recently_turning_hub_is_ok_even_with_work():
    # turned 10m ago -> alive and reconciling; not deaf even though work is queued.
    v, _ = m.classify_session_liveness(lease_fresh=True, last_activity_age_s=10 * M,
                                       work_waiting=True, threshold_s=45 * M)
    assert v == "ok"


def test_lease_not_fresh_is_host_down_not_this_check():
    # host itself is down -> fleet_health_lease reclaim's job, NOT the session-deaf page (avoid
    # double-signalling; this check is specifically the alive-host/dead-session hole).
    v, _ = m.classify_session_liveness(lease_fresh=False, last_activity_age_s=99 * M,
                                       work_waiting=True, threshold_s=45 * M)
    assert v == "host_down"


def test_threshold_is_exclusive():
    at, _ = m.classify_session_liveness(True, 45 * M, True, 45 * M)
    over, _ = m.classify_session_liveness(True, 45 * M + 1, True, 45 * M)
    assert at == "ok" and over == "session_deaf"


# ── session-deaf PAGE path (prove the loud path deterministically — fake cursor, no DB) ──
class _FakeCur:
    def __init__(self):
        self.inserts = []
        self.last = None
    def execute(self, sql, params=None):
        self.last = (sql, params)
        if sql.lstrip().upper().startswith("INSERT"):
            self.inserts.append((sql, params))
    def fetchone(self):
        return None


def test_session_deaf_page_writes_p1_to_both_recipients():
    cur = _FakeCur()
    sent = m._page_session_deaf(cur, last_activity_age_s=90 * 60.0, reason="host up, no turn 90m")
    assert sent == len(m.RECIPIENTS) == 2
    assert len(cur.inserts) == 2
    for sql, params in cur.inserts:
        assert "INSERT INTO agent_messages" in sql
        assert "'P1'" in sql                       # session-deaf pages at P1 (operator may be unheard)
        assert params[2].startswith("HUB SESSION-DEAF:")   # subject dedup marker
        assert params[0] == m.SRE_FROM


def test_session_deaf_dedup_queries_the_marker():
    cur = _FakeCur()
    m._already_paged_session_deaf(cur, 150)
    sql, params = cur.last
    assert "HUB SESSION-DEAF:%" in sql and params[0] == m.SRE_FROM


# ── fix (2026-09-05 false positive): liveness = FRESHEST of the hub's session signals, not just
#    the agent-bus turn. The hub goes >45m without a bus turn while alive+working the operator
#    channel; its ctx-publish (jsonl-growing) stays fresh. Feed the min age so bus-quiet≠deaf. ──
def test_freshest_age_takes_the_minimum_non_none():
    assert m._freshest_age([73 * 60, 39 * 60]) == 39 * 60
    assert m._freshest_age([None, 39 * 60]) == 39 * 60
    assert m._freshest_age([73 * 60, None]) == 73 * 60
    assert m._freshest_age([None, None]) is None


def test_bus_quiet_but_ctx_publish_fresh_is_not_deaf():
    # THE false positive: no agent-bus turn for 73m, but ctx-publish 39m fresh (session alive via
    # the operator channel). Fed the FRESHEST activity age (39m) -> OK even with work queued.
    v, _ = m.classify_session_liveness(lease_fresh=True, last_activity_age_s=39 * 60,
                                       work_waiting=True, threshold_s=45 * 60)
    assert v == "ok"


def test_both_signals_stale_with_work_is_still_deaf():
    # the REAL incident shape: bus AND ctx-publish both stale (session truly stuck) + work waiting.
    v, _ = m.classify_session_liveness(lease_fresh=True, last_activity_age_s=90 * 60,
                                       work_waiting=True, threshold_s=45 * 60)
    assert v == "session_deaf"
