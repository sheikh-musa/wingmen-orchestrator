"""SLA watchdog false-page + dedup fixes (Nazim #63881, incident op#29308).

FIX 1: a recipient that replies IN THE ITEM'S THREAD after the item is attending it,
even on an 'unread' violation (it stamps read_at late). Suppress — but ONLY for a
same-thread reply, so an old-topic reply in a DIFFERENT thread cannot mask a
genuinely-unread P0 (the #63850 false-page: 9 thread replies, read_at ~14min late).

FIX 2: already_paged_on_bus must recognize the ACTUAL internal-escalation row
('SLA stall: <agent> #<mid> (...)'), not only the operator-page audit subject —
else CAI-600 internal escalations never dedupe and re-page every ~90s scan
(#63877 @12m, #63880 @13m on the same #63850).
"""
from scripts import priority_sla_watchdog as s

HUB = s.HUB_SESSIONS


# ---- FIX 1: _attended_decision ----
def test_samethread_reply_suppresses_unread():
    assert s._attended_decision(is_read=False, is_resp=False, violation_type="unread",
                                agent="x", same_thread_reply_after=True, hub_sessions=HUB) is True


def test_different_thread_reply_does_not_mask_unread_p0():
    # same_thread_reply_after is False because the only reply was in a DIFFERENT thread
    assert s._attended_decision(is_read=False, is_resp=False, violation_type="unread",
                                agent="x", same_thread_reply_after=False, hub_sessions=HUB) is False


def test_responded_always_attended():
    assert s._attended_decision(is_read=False, is_resp=True, violation_type="unresponded",
                                agent="x", same_thread_reply_after=False, hub_sessions=HUB) is True


def test_unread_read_cleared():
    assert s._attended_decision(is_read=True, is_resp=False, violation_type="unread",
                                agent="x", same_thread_reply_after=False, hub_sessions=HUB) is True


def test_unresponded_only_read_normal_lane_still_pages():
    assert s._attended_decision(is_read=True, is_resp=False, violation_type="unresponded",
                                agent="x", same_thread_reply_after=False, hub_sessions=HUB) is False


def test_hub_read_suppressed():
    hub_agent = next(iter(HUB))
    assert s._attended_decision(is_read=True, is_resp=False, violation_type="unresponded",
                                agent=hub_agent, same_thread_reply_after=False, hub_sessions=HUB) is True


# ---- FIX 2: _subject_is_prior_page ----
def test_escalation_subject_matches_mid():
    assert s._subject_is_prior_page("SLA stall: cc-cosem-adcda-3 #63850 (P0, 12m)", 63850) is True


def test_audit_subject_matches_mid():
    assert s._subject_is_prior_page("[sla-watchdog] PAGED operator re msg #63850", 63850) is True


def test_substring_mid_does_not_falsematch():
    assert s._subject_is_prior_page("SLA stall: x #963850 (P0, 1m)", 63850) is False


def test_unrelated_subject_no_match():
    assert s._subject_is_prior_page("deploy finished for #63850-ish thing", 63850) is False
