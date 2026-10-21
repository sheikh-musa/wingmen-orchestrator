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


MUSA_ID = "286619815"  # MUSA_TELEGRAM_ID (migrations/020), matches tests/test_stage_client_file.py


def _row(mid, direction, age_s, text="hi", from_user_id=None):
    return {"id": mid, "direction": direction, "created_epoch": NOW - age_s,
            "from_name": "Fazli" if direction == "inbound" else "coord", "text": text,
            "from_user_id": from_user_id}


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


def test_operator_misfired_inbound_is_not_waiting_72953():
    # orch-console #72953 (2nd occurrence): Musa's OWN operator message, misfired into a
    # client channel, is NOT a client waiting for a reply -- it must never surface here.
    # musa_id passed EXPLICITLY (not relying on an ambient MUSA_TELEGRAM_ID env var, which
    # is present on a dev machine but NOT in CI -- this test must be hermetic).
    rows = [_row(32580, "inbound", 3600, "oops wrong channel", from_user_id=MUSA_ID)]
    assert nd.latest_unanswered_inbound(rows, musa_id=MUSA_ID) is None


def test_operator_misfired_inbound_does_not_hide_a_real_older_unanswered_client_msg():
    # the operator-sent row is skipped entirely (not even treated as "answered") so a
    # GENUINE older unanswered client inbound underneath it still surfaces.
    rows = [
        _row(32580, "inbound", 60, "oops wrong channel", from_user_id=MUSA_ID),
        _row(32500, "inbound", 3600, "any update on my request?"),
    ]
    w = nd.latest_unanswered_inbound(rows, musa_id=MUSA_ID)
    assert w is not None and w["id"] == 32500


def test_operator_id_default_none_still_counts_as_client_unanswered():
    # a row with no from_user_id (historical rows, pre-migration-020) is NOT treated as the
    # operator -- must not silently stop paging for every pre-existing channel.
    assert nd.latest_unanswered_inbound([_row(1, "inbound", 60)])["id"] == 1


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


# ---- load_channels() discovery (fable-audit-20261006 finding 12 / held_commitment #314) ----
# The filter must key on audience='client' (the schema's actual client marker), not a `cc-*`
# naming convention on the coord — that heuristic missed cosem-caai/cosem-exams (routed
# straight to orch-console, no separate coord) and would wrongly include an internal channel
# that happens to route to a cc-* agent (e.g. mamadah).
class _FakeCursor:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    def execute(self, sql, *a):
        self._captured.append(sql)

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    def cursor(self):
        return _FakeCursor(self._rows, self._captured)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_load_channels_query_keys_on_audience_client(monkeypatch):
    captured = []
    rows = [("cosem-tdu", "cc-cosem-tdu-coord"), ("cosem-caai", "orch-console"),
            ("cosem-exams", "orch-console")]
    monkeypatch.setattr(nd.psycopg2, "connect", lambda dsn: _FakeConn(rows, captured))

    out = nd.load_channels("fake-dsn")

    assert "audience='client'" in captured[0]
    assert "LIKE 'cc-%'" not in captured[0]
    tags = {c["tag"] for c in out}
    assert tags == {"cosem-tdu", "cosem-caai", "cosem-exams"}


def test_load_channels_console_routed_channel_keeps_console_as_coord(monkeypatch):
    rows = [("cosem-caai", "orch-console")]
    monkeypatch.setattr(nd.psycopg2, "connect", lambda dsn: _FakeConn(rows, []))

    out = nd.load_channels("fake-dsn")

    assert out[0]["coord"] == "orch-console"
    assert out[0]["send_script"] == "scripts/caai_send.sh"


def test_load_channels_excludes_gazzabyte_dedicated_watch(monkeypatch):
    rows = [("gazzabyte-irsyad", "cc-irsyad-coord"), ("oeh", "cc-oeh")]
    monkeypatch.setattr(nd.psycopg2, "connect", lambda dsn: _FakeConn(rows, []))

    out = nd.load_channels("fake-dsn")

    assert {c["tag"] for c in out} == {"oeh"}


def test_load_channels_db_error_falls_back_to_static_list(monkeypatch):
    def _boom(dsn):
        raise RuntimeError("db down")
    monkeypatch.setattr(nd.psycopg2, "connect", _boom)

    out = nd.load_channels("fake-dsn")

    assert out == list(nd.FALLBACK_CHANNELS)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
