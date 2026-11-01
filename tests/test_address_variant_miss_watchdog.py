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
    _nudge_lane,
    due_for_page,
    find_address_variant_misses,
    lane_nudge_message,
    page_body,
    page_subject,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _row(instance_id, base_id, unread_age_min, base_read_age_min, unread_count=1,
         instance_status="working", tmux_session=None):
    return {
        "instance_id": instance_id,
        "base_agent_id": base_id,
        "instance_status": instance_status,
        "tmux_session": tmux_session,
        "oldest_unread_created_at": NOW - timedelta(minutes=unread_age_min) if unread_age_min is not None else None,
        "unread_count": unread_count,
        "last_base_read_at": NOW - timedelta(minutes=base_read_age_min) if base_read_age_min is not None else None,
    }


def test_fires_on_the_real_2026_10_04_scholar_incident():
    """Replay of the actual bus #51166 incident (orch-console #51195 ask):
    cc-scholar-1 had 5 unread rows #51058/51076/51099/51118/51156 created
    11:17:08-11:59:53Z, all sitting unread until 12:04:16Z (the fix/re-nudge),
    while cc-scholar (base) read #51081 (11:33:17) and #51095 (11:40:33) in
    the same window -- proof it was awake, just blind to its own instance
    address. Evaluated as of 12:00:00Z, mid-incident, before the 12:04:16 read."""
    incident_now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
    row = {
        "instance_id": "cc-scholar-1",
        "base_agent_id": "cc-scholar",
        "instance_status": "working",  # scholar-1 was live and stuck, not retired
        "oldest_unread_created_at": datetime(2026, 10, 4, 11, 17, 8, tzinfo=timezone.utc),
        "unread_count": 5,
        "last_base_read_at": datetime(2026, 10, 4, 11, 40, 33, tzinfo=timezone.utc),
    }
    misses = find_address_variant_misses([row], now=incident_now)
    assert len(misses) == 1
    assert misses[0]["instance_id"] == "cc-scholar-1"


def test_ignores_offline_instance_even_if_base_is_awake():
    """cc-quality #51199's finding, closed: the live gap they found was
    cc-quality-1 (base cc-quality) sitting status='offline' with a stale
    last_heartbeat (~11h) -- a genuinely retired instance, not a live one
    blind to its own inbox. A base being awake only proves the FAMILY is
    awake, not that any specific past instance still exists. This replays
    that exact identity shape with old unread mail to prove it would NOT
    misfire now that instance_status is checked."""
    row = _row("cc-quality-1", "cc-quality", unread_age_min=60, base_read_age_min=5,
               instance_status="offline")
    assert find_address_variant_misses([row], now=NOW) == []


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


# ── bus#58273 (held_commitment #360): direct lane nudge, additive to the
# console page ────────────────────────────────────────────────────────────

def test_lane_nudge_message_names_the_instance_and_the_fix():
    miss = _row("cc-shipforge-1", "cc-shipforge", unread_age_min=30, base_read_age_min=5,
                unread_count=3, tmux_session="shipforge")
    msg = lane_nudge_message(miss)
    assert "cc-shipforge-1" in msg
    assert "cc-shipforge" in msg
    assert "to_agent IN (base, instance)" in msg
    assert "3" in msg  # unread_count


def test_nudge_lane_skips_cleanly_with_no_tmux_session_on_record():
    """A miss row with no registered tmux_session (agent_status.tmux_session
    NULL) must not attempt a nudge at all -- there is nothing to send-keys
    into, and this must be a clean skip, never a crash or a hang."""
    miss = _row("cc-shipforge-1", "cc-shipforge", unread_age_min=30, base_read_age_min=5,
                tmux_session=None)
    assert _nudge_lane(miss) is False


def test_nudge_lane_calls_lane_nudge_sh_with_session_and_message(tmp_path):
    """_nudge_lane must invoke the sanctioned lane_nudge.sh wrapper (never a
    raw tmux send-keys) with (tmux_session, message) -- verified against a
    fake binary that records its argv and exits 0 (verified-submit success)."""
    fake_bin = tmp_path / "lane_nudge.sh"
    record = tmp_path / "called_with.txt"
    fake_bin.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$1" "$2" > "{record}"\n'
        "exit 0\n"
    )
    fake_bin.chmod(0o755)

    miss = _row("cc-shipforge-1", "cc-shipforge", unread_age_min=30, base_read_age_min=5,
                unread_count=2, tmux_session="shipforge")
    assert _nudge_lane(miss, lane_nudge_bin=fake_bin) is True

    lines = record.read_text().splitlines()
    assert lines[0] == "shipforge"  # tmux session, exact
    assert "cc-shipforge-1" in lines[1]  # the message body mentions the instance


def test_nudge_lane_false_on_non_zero_exit_never_raises(tmp_path):
    """lane_nudge.sh exit codes 2/3/5/6/7/8 are all REFUSED/failed-delivery
    shapes (menu-parked, unreadable pane, ceiling hit, ...) -- _nudge_lane
    must report False for any of them, never raise, and never be mistaken
    for a successful delivery."""
    fake_bin = tmp_path / "lane_nudge.sh"
    fake_bin.write_text("#!/usr/bin/env bash\nexit 5\n")  # 5 = menu-parked refusal
    fake_bin.chmod(0o755)

    miss = _row("cc-shipforge-1", "cc-shipforge", unread_age_min=30, base_read_age_min=5,
                tmux_session="shipforge")
    assert _nudge_lane(miss, lane_nudge_bin=fake_bin) is False


def test_ignores_lane_that_read_its_instance_mail_since_oldest_unread_80448():
    """2026-10-28 #80448 false positive: cc-cosem-platform-1 left two P3 FYIs (80016 at
    12:40Z, 80066 at 13:05Z) unread for ~5h while reading P2 rows addressed to its
    INSTANCE id within minutes (80179 read 14:36Z, 80311 read 16:10Z). Reading
    instance-addressed mail after the oldest unread one landed proves the lane SEES its
    instance address -- the backlog is priority ordering, not blindness."""
    now = datetime(2026, 10, 28, 17, 57, tzinfo=timezone.utc)
    row = {
        "instance_id": "cc-cosem-platform-1",
        "base_agent_id": "cc-cosem-platform",
        "instance_status": "working",
        "oldest_unread_created_at": datetime(2026, 10, 28, 12, 40, 40, tzinfo=timezone.utc),
        "unread_count": 3,
        "last_base_read_at": datetime(2026, 10, 28, 17, 47, 27, tzinfo=timezone.utc),
        "last_instance_read_at": datetime(2026, 10, 28, 16, 10, 31, tzinfo=timezone.utc),
    }
    assert find_address_variant_misses([row], now=now) == []


def test_instance_read_BEFORE_oldest_unread_does_not_veto():
    """An instance read that predates the oldest unread row says nothing about the
    current window -- still a miss (blindness can start mid-session, #51166 shape)."""
    row = _row("cc-scholar-1", "cc-scholar", unread_age_min=45, base_read_age_min=5)
    row["last_instance_read_at"] = row["oldest_unread_created_at"] - timedelta(minutes=10)
    assert len(find_address_variant_misses([row], now=NOW)) == 1


def test_a_prior_instance_read_is_carried_into_the_miss_for_the_diagnosis():
    """2026-11-01 cc-cosem-adcda-2: it read its own instance-addressed mail
    85 seconds BEFORE the unread row landed, so it was never blind to the
    address. The miss stands (the row still sat unanswered) but must carry
    the evidence, because the page used to tell the lane to fix a reader that
    was working."""
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["last_instance_read_at"] = row["oldest_unread_created_at"] - timedelta(seconds=85)
    misses = find_address_variant_misses([row], now=NOW)
    assert len(misses) == 1
    assert abs(misses[0]["prior_instance_read_age_min"] - 85 / 60) < 0.01


def test_a_lane_that_never_read_its_instance_address_carries_no_prior_read():
    row = _row("cc-scholar-1", "cc-scholar", unread_age_min=45, base_read_age_min=5)
    misses = find_address_variant_misses([row], now=NOW)
    assert misses[0]["prior_instance_read_age_min"] is None


def test_the_page_does_not_claim_blindness_when_the_instance_read_first():
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["last_instance_read_at"] = row["oldest_unread_created_at"] - timedelta(seconds=85)
    miss = find_address_variant_misses([row], now=NOW)[0]
    body = page_body(miss)
    assert "blind to its own instance address" not in page_body(miss)
    assert "NOT a base-only reader" in body
    assert "claude" not in body.lower()  # no stray provider/model claims in an operator page
    assert "blind to its own inbox" not in page_subject(miss)


def test_the_lane_nudge_does_not_send_it_after_a_working_filter():
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["last_instance_read_at"] = row["oldest_unread_created_at"] - timedelta(seconds=85)
    miss = find_address_variant_misses([row], now=NOW)[0]
    msg = lane_nudge_message(miss)
    assert "probably NOT" in msg
    assert "my_inbox.py" in msg
    # it still leaves the door open for a genuine regression
    assert "base-only filter" in msg


def test_the_blind_case_still_gets_the_original_instruction():
    row = _row("cc-scholar-1", "cc-scholar", unread_age_min=45, base_read_age_min=5)
    miss = find_address_variant_misses([row], now=NOW)[0]
    assert "blind to its own inbox" in page_subject(miss)
    assert "to_agent IN (base, instance)" in lane_nudge_message(miss)


def _soft_row(unread_age_min):
    """A miss in the soft (prior instance read) shape, at a given row age."""
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=unread_age_min,
               base_read_age_min=5)
    row["last_instance_read_at"] = row["oldest_unread_created_at"] - timedelta(minutes=2)
    return row


def test_the_row_carries_how_many_cooldowns_it_has_survived_from_its_own_age():
    """Derived from the ROW's age, not wall-clock or page history, so a daemon
    restart cannot reset the escalation (cc-quality 89642)."""
    miss = find_address_variant_misses([_soft_row(unread_age_min=500)], now=NOW)[0]
    assert abs(miss["cooldowns_unread"] - 500 / 240) < 1e-9


def test_a_young_soft_row_still_gets_the_plain_soft_diagnosis():
    miss = find_address_variant_misses([_soft_row(unread_age_min=30)], now=NOW)[0]
    assert "cooldown" not in page_body(miss)
    assert "regressed" not in page_body(miss)
    assert "still unread after" not in page_subject(miss)


def test_a_soft_row_past_the_escalation_bar_names_the_regression_risk():
    miss = find_address_variant_misses([_soft_row(unread_age_min=500)], now=NOW)[0]
    assert "STILL unread" in page_body(miss)
    assert "regressed to base-only" in page_body(miss)
    assert "Verify the lane's filter" in page_body(miss)
    assert "check for a reader regression" in page_subject(miss)
    # the lane itself is told the same thing, in second person
    assert "stop assuming the stopped-turn story" in lane_nudge_message(miss)


def test_the_escalation_never_affects_whether_it_fires():
    """A silencer must fail toward still-alerting: the escalated row is the SAME
    miss, still returned, still paged."""
    for age in (30, 500, 5000):
        assert len(find_address_variant_misses([_soft_row(unread_age_min=age)], now=NOW)) == 1


def test_the_blind_branch_is_untouched_by_the_escalation():
    row = _row("cc-scholar-1", "cc-scholar", unread_age_min=5000, base_read_age_min=5)
    miss = find_address_variant_misses([row], now=NOW)[0]
    assert page_subject(miss) == "address-variant miss: cc-scholar-1 blind to its own inbox"
    assert "cooldown" not in page_body(miss)


# ------------------------------------------- sibling-attributed base reads (89662)

def test_a_sibling_base_read_cannot_keep_an_instance_looking_awake():
    """2026-11-01 #89443: 89580 was addressed to the BASE and read at 09:30:30 by a
    DIFFERENT adcda instance, which supplied the 'awake and draining' proof for
    cc-cosem-adcda-2. With siblings alive, the base read is not attributable."""
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["family_live_instances"] = 3
    row["instance_last_heartbeat"] = NOW - timedelta(hours=4)   # this one is not live
    assert find_address_variant_misses([row], now=NOW) == []


def test_a_multi_instance_family_still_pages_on_the_instances_own_heartbeat():
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["family_live_instances"] = 3
    row["instance_last_heartbeat"] = NOW - timedelta(minutes=3)
    misses = find_address_variant_misses([row], now=NOW)
    assert len(misses) == 1
    assert misses[0]["liveness_basis"] == "instance-heartbeat"


def test_a_single_instance_family_keeps_using_the_base_read():
    row = _row("cc-solo-1", "cc-solo", unread_age_min=45, base_read_age_min=5)
    row["family_live_instances"] = 1
    misses = find_address_variant_misses([row], now=NOW)
    assert len(misses) == 1 and misses[0]["liveness_basis"] == "base-read"


def test_unknown_family_size_keeps_the_original_behaviour():
    """No family_live_instances (an older caller / a partial query) must not
    silently stop paging."""
    row = _row("cc-scholar-1", "cc-scholar", unread_age_min=45, base_read_age_min=5)
    assert len(find_address_variant_misses([row], now=NOW)) == 1


def test_the_heartbeat_branch_does_not_claim_a_base_read_happened():
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["family_live_instances"] = 3
    row["instance_last_heartbeat"] = NOW - timedelta(minutes=3)
    miss = find_address_variant_misses([row], now=NOW)[0]
    body = page_body(miss)
    assert "while it read mail on its base id" not in body
    assert "a base read cannot be attributed to this one" in body
    assert "blind" in page_subject(miss)


def test_a_p3_only_backlog_is_not_a_miss():
    """Fleet-wide, P3 never wakes a lane (agent_wake + wake_backstop_sweep both drop
    it), so a P3-only backlog is priority ordering, not blindness. This is a SQL
    filter, so prove the predicate's shape here: the watchdog is fed by QUERY."""
    from scripts.address_variant_miss_watchdog import QUERY
    assert "priority IS DISTINCT FROM 'P3'" in QUERY
    assert QUERY.count("read_at IS NULL") >= 1


def test_the_heartbeat_bar_is_the_fleets_liveness_bar_not_the_read_window():
    """A lane the fleet calls live (hb < 30m) and that has not read its own address
    must still page. Keying this to the 20-min READ window would suppress it."""
    from scripts.address_variant_miss_watchdog import HEARTBEAT_RECENCY_MIN
    assert HEARTBEAT_RECENCY_MIN >= 30
    row = _row("cc-cosem-adcda-2", "cc-cosem-adcda", unread_age_min=81, base_read_age_min=11)
    row["family_live_instances"] = 3
    row["instance_last_heartbeat"] = NOW - timedelta(minutes=25)  # fleet-live, read-window-stale
    assert len(find_address_variant_misses([row], now=NOW)) == 1
