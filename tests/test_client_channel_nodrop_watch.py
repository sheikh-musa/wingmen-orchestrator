"""Tests for client_channel_nodrop_watch — the receipt-aware no-drop core.

Critical (Nazim 43631): the ingest auto-posts a "📨 Got your message" RECEIPT into client
groups; a receipt is NOT a real reply and must NOT clear the drop. These lock:
  - is_receipt() matches the ingest marker,
  - latest_unanswered_inbound() treats a receipt-only follow-up as STILL UNANSWERED,
  - evaluate() nudges the coord then escalates to console, operator never targeted.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import client_channel_nodrop_watch as nd  # noqa: E402

NOW = 1_000_000_000.0
NUDGE = 30 * 60
ESC = 2 * 3600
RENUDGE = 60 * 60
MAX_LIVE = 7 * 86400
COORD = "cc-cosem-tdu-coord"
LABEL = "cosem-tdu"
TAG = "cosem-tdu"
SEND = "scripts/cosem_tdu_support_send.sh"

RECEIPT = "📨 Got your message — it's logged and I'll pick it up. (Reply URGENT to bump it now.)"


def _row(mid, direction, age_s, text="hi"):
    return {"id": mid, "direction": direction, "created_epoch": NOW - age_s,
            "from_name": "Fazli" if direction == "inbound" else "coord", "text": text}


# ---- is_receipt --------------------------------------------------------------
def test_is_receipt_matches_ingest_marker():
    assert nd.is_receipt(RECEIPT) is True
    assert nd.is_receipt("📨 Got your message — I'm mid-task right now, I'll reply at my next pause.") is True


def test_is_receipt_false_for_a_real_reply():
    assert nd.is_receipt("Yes — the deploy is unblocked, I'll ship it now.") is False
    assert nd.is_receipt(None) is False


# ---- latest_unanswered_inbound (receipt-aware) -------------------------------
def test_inbound_then_receipt_only_is_UNANSWERED():
    # THE regression Nazim flagged: a receipt must NOT count as answered.
    rows = [_row(22456, "outbound", 60, RECEIPT), _row(22517, "inbound", 120, "any update?")]
    w = nd.latest_unanswered_inbound(rows)
    assert w is not None and w["id"] == 22517


def test_inbound_then_real_reply_is_answered():
    rows = [_row(22600, "outbound", 60, "shipped, all good"), _row(22517, "inbound", 120)]
    assert nd.latest_unanswered_inbound(rows) is None


def test_inbound_only_is_unanswered():
    assert nd.latest_unanswered_inbound([_row(22517, "inbound", 120)])["id"] == 22517


def test_no_inbound_or_empty_is_none():
    assert nd.latest_unanswered_inbound([]) is None
    assert nd.latest_unanswered_inbound([_row(1, "outbound", 60, "broadcast")]) is None


def test_older_answered_then_new_inbound_plus_receipt_is_unanswered():
    # a fully-handled older exchange, then a NEW client message with only a receipt after it
    rows = [
        _row(30, "outbound", 30, RECEIPT),       # receipt for the new inbound
        _row(29, "inbound", 60, "new question"),  # the new (unanswered) client message
        _row(20, "outbound", 3600, "real answer to the old one"),
        _row(19, "inbound", 3700, "old question"),
    ]
    w = nd.latest_unanswered_inbound(rows)
    assert w is not None and w["id"] == 29


# ---- evaluate ----------------------------------------------------------------
def ev(waiting, state):
    return nd.evaluate(waiting, NOW, state, coord=COORD, label=LABEL, tag=TAG, send_script=SEND,
                       nudge_s=NUDGE, escalate_s=ESC, re_nudge_s=RENUDGE)


def test_waiting_past_window_nudges_coord_with_send_script():
    actions, st = ev(_row(22517, "inbound", NUDGE + 60, "any update?"), {})
    assert len(actions) == 1 and actions[0]["to"] == COORD
    assert SEND in actions[0]["body"]
    assert st["22517"]["coord_nudge_count"] == 1


def test_none_waiting_no_action_and_clears():
    actions, st = ev(None, {"22517": {"coord_nudged_at": NOW - 100}})
    assert actions == [] and st == {}


def test_fresh_inbound_no_nudge():
    assert ev(_row(22517, "inbound", NUDGE - 60), {})[0] == []


def test_coord_nudge_deduped_within_backoff():
    assert ev(_row(22517, "inbound", NUDGE + 60), {"22517": {"coord_nudged_at": NOW - 60}})[0] == []


def test_console_backstop_only_after_prior_coord_cycle():
    prior = {"22517": {"coord_nudged_at": NOW - RENUDGE - 1, "coord_nudge_count": 1}}
    actions, st = ev(_row(22517, "inbound", ESC + 60), prior)
    tos = {a["to"] for a in actions}
    assert COORD in tos and "orch-console" in tos
    assert st["22517"]["console_escalated_at"] == NOW


def test_no_console_escalation_on_first_ever_cycle():
    assert {a["to"] for a in ev(_row(22517, "inbound", ESC + 60), {})[0]} == {COORD}


def test_operator_is_never_a_target():
    prior = {"22517": {"coord_nudged_at": NOW - RENUDGE - 1, "coord_nudge_count": 1}}
    for age in (NUDGE + 60, ESC + 60):
        for a in ev(_row(22517, "inbound", age), prior)[0]:
            assert a["to"] in (COORD, "orch-console")


def test_stale_backlog_reports_once_to_console_no_coord_nudge():
    # Nazim 43634: an inbound older than the 7d live horizon is NOT a live drop — report ONCE
    # to console (no coord nudge). (The hk-editor op#18003 ~27d done-but-unacked case.)
    actions, st = ev(_row(18003, "inbound", MAX_LIVE + 86400, "old request"), {})
    assert len(actions) == 1
    assert actions[0]["to"] == "orch-console"
    assert "STALE BACKLOG" in actions[0]["subject"]
    assert st["18003"]["stale_reported_at"] == NOW


def test_stale_backlog_not_repeated_after_first_report():
    prior = {"18003": {"stale_reported_at": NOW - 10}}
    actions, st = ev(_row(18003, "inbound", MAX_LIVE + 86400), prior)
    assert actions == []            # reported once already — no repeat
    assert st["18003"]["stale_reported_at"] == NOW - 10


def test_just_under_horizon_still_nudges_coord_not_stale():
    # a 6-day-old drop is still LIVE -> coord nudge, not a stale-backlog report
    actions, _ = ev(_row(22517, "inbound", MAX_LIVE - 86400, "still waiting"), {})
    assert actions and actions[0]["to"] == COORD


def test_multichannel_coord_and_label_flow_through():
    actions, _ = nd.evaluate(_row(1, "inbound", NUDGE + 60), NOW, {}, coord="cc-angullia",
                             label="angullia", tag="angullia", send_script="x",
                             nudge_s=NUDGE, escalate_s=ESC, re_nudge_s=RENUDGE)
    assert actions[0]["to"] == "cc-angullia" and "angullia" in actions[0]["body"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
