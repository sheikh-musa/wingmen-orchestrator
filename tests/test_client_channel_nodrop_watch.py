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


# ---- _musa_telegram_id (env-then-file fallback, mirrors _dsn's shape) --------
def test_musa_telegram_id_reads_env_var_first(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", MUSA_ID)
    assert nd._musa_telegram_id() == MUSA_ID


def test_musa_telegram_id_falls_back_to_env_file(monkeypatch, tmp_path):
    # orch-console #72969: the deployed launchd plist never sources .env (unlike other
    # client-nodrop plists), so MUSA_TELEGRAM_ID is genuinely unset in the real cron's
    # process environment -- the sender-check (PR#452) silently never fired in production.
    # Same fallback shape _dsn() already has for DATABASE_URL.
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL=postgres://x\nMUSA_TELEGRAM_ID={MUSA_ID}\n")
    monkeypatch.setattr(nd, "ORCH", str(tmp_path))
    assert nd._musa_telegram_id() == MUSA_ID


def test_musa_telegram_id_returns_none_when_absent_everywhere(monkeypatch, tmp_path):
    # fail-safe: absent entirely -> None (never skips every channel's drop-watch silently;
    # is_operator_sender's own fail-safe already handles musa_id=None).
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgres://x\n")
    monkeypatch.setattr(nd, "ORCH", str(tmp_path))
    assert nd._musa_telegram_id() is None


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


# ---- non-text / service updates are NOT client messages (orch-console #89481) -----------
# THE false positive: op#37155 is a Telegram PIN event. Its only payload key was
# pinned_message, so ingest stored its last-resort shape marker, and the watch read that
# row as "Hariz's latest message, unanswered for 33 minutes" — while his real message
# 37148 had been answered twice (37149, 37150) nine minutes earlier.
PIN_37155 = "[non-text update 50118302 — keys: chat,date,from,message_id,pinned_message]"


def test_is_non_text_update_matches_the_real_marker():
    assert nd.is_non_text_update(PIN_37155) is True
    # bare form ingest emits when content extraction raised and there was no text
    assert nd.is_non_text_update("[non-text update 14]") is True
    # fetch_recent reads LEFT(text,240): a long key list cuts the closing bracket
    truncated = "[non-text update 50118302 — keys: " + ",".join(f"k{i}" for i in range(100))
    assert len(truncated) > 240
    assert nd.is_non_text_update(truncated[:240]) is True


def test_a_real_message_is_not_swept_up_by_the_marker():
    # anchored at the start: a client may quote the marker back at us
    assert nd.is_non_text_update("why did you send me [non-text update 12]?") is False
    assert nd.is_non_text_update("As of today may i know the list of assigned staffer") is False
    assert nd.is_non_text_update("") is False and nd.is_non_text_update(None) is False


def test_pin_after_an_answered_message_is_not_a_drop():
    # EXACT shape of the 2026-11-01 rows, newest-first.
    rows = [
        _row(37155, "inbound", 33 * 60, PIN_37155),        # pin event, no text/caption/media
        _row(37150, "outbound", 42 * 60, "One addition to point 5, on how a role gets assigned"),
        _row(37149, "outbound", 43 * 60, "Answers, all read from the live system at 07:50 UTC"),
        _row(37148, "inbound", 47 * 60, "As of today may i know the list of assigned staffer"),
    ]
    assert nd.latest_unanswered_inbound(rows) is None


def test_pin_event_does_not_hide_an_older_unanswered_message():
    # Looking back must still FIND a genuine drop sitting under the pin.
    rows = [
        _row(37155, "inbound", 60, PIN_37155),
        _row(37148, "inbound", 600, "As of today may i know the list of assigned staffer"),
    ]
    w = nd.latest_unanswered_inbound(rows)
    assert w is not None and w["id"] == 37148


def test_pin_event_alone_is_not_a_drop():
    assert nd.latest_unanswered_inbound([_row(37155, "inbound", 60, PIN_37155)]) is None


def test_a_captionless_photo_still_counts_as_a_client_message():
    # Media DOES get a real handler, so it never lands as the marker — a client sending a
    # picture is a genuine inbound and must keep paging.
    rows = [_row(37200, "inbound", 600, "📷 Photo received (image/jpeg) — saved as media/abc.jpg")]
    w = nd.latest_unanswered_inbound(rows)
    assert w is not None and w["id"] == 37200


# ---- the conversation is the CHAT, not the tag (orch-console #89533 / cc-quality 89559) --
# THE miss: replies to Hariz were sent under topic tags (attempts-staff-20261101,
# role-assignment-20261101) while his message carried the channel tag, so `WHERE tag=%s`
# could not see them and a well-answered message read as unanswered.
# THE REGRESSION my first attempt shipped: resolving the chat set from "every chat that
# carried this tag" pulled in the operator's own DM (they tag by topic there), which is the
# busiest chat in the system — hk-editor and oeh became 25-of-25 DM rows and went SILENT.
MUSA = "286619815"
CLIENT_CHAT = -5390372474


class _ChatFakeCursor:
    """Returns queued result sets in order, one per execute()."""

    def __init__(self, results):
        self.results, self.sqls, self.params = list(results), [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sqls.append(" ".join(sql.split()))
        self.params.append(params)

    def fetchall(self):
        return self.results.pop(0) if self.results else []


class _ChatFakeConn:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cur


def _fetch_with(monkeypatch, results):
    cur = _ChatFakeCursor(results)
    monkeypatch.setattr(nd.psycopg2, "connect", lambda *a, **k: _ChatFakeConn(cur))
    return nd.fetch_recent("postgresql://ignored", "cosem-exams", musa_id=MUSA), cur


def test_fetch_recent_reads_the_allowlist_declared_chat(monkeypatch):
    """PRIMARY path: resolve chats from bot_channels.allowed_chat_ids, the authoritative map."""
    out, cur = _fetch_with(monkeypatch, [[([CLIENT_CHAT],)], []])
    assert "FROM bot_channels" in cur.sqls[0] and "allowed_chat_ids" in cur.sqls[0]
    assert cur.params[0] == ("cosem-exams",)
    # the message read is scoped to that chat list, and carries chat_id back for the guard
    assert cur.params[1][0] == [str(CLIENT_CHAT)]   # chat_id is TEXT on operator_messages
    assert "chat_id" in cur.sqls[1]
    assert out == []


def test_a_tagged_operator_dm_can_never_join_the_chat_set(monkeypatch):
    """THE REGRESSION GUARD. The operator tags their own DM by topic, so the DM carries a row
    for nearly every channel tag. It must not enter the chat set — if it does, its volume
    fills the window and the client is crowded out entirely (silent no-drop)."""
    _out, cur = _fetch_with(monkeypatch, [[([CLIENT_CHAT],)], []])
    chats = cur.params[1][0]
    assert MUSA not in chats, (
        "the operator's own DM must never be treated as the client's conversation; it is the "
        "busiest chat in the system and crowds every client row out of the window"
    )
    assert "allowed_chat_ids" in cur.sqls[0], (
        "the chat set must come from the declared allowlist, not from rows that happen to "
        "share the tag"
    )


def test_fallback_drops_the_operator_dm_when_no_allowlist_exists(monkeypatch):
    """bariq has an empty allowed_chat_ids today: fall back to tag-bearing chats, minus the DM."""
    results = [[], [(int(MUSA),), (CLIENT_CHAT,)], []]
    _out, cur = _fetch_with(monkeypatch, results)
    chats = cur.params[-1][0]
    assert MUSA not in chats and str(CLIENT_CHAT) in chats
    assert cur.params[-1][1] == "cosem-exams"     # NULL-chat_id rows still scoped to the tag


# ---- the answer guard: same chat only ---------------------------------------------------
def _r(mid, direction, age_s, text="hi", chat_id=CLIENT_CHAT, from_user_id=None):
    row = _row(mid, direction, age_s, text, from_user_id=from_user_id)
    row["chat_id"] = chat_id
    return row


def test_is_same_chat():
    assert nd.is_same_chat({"chat_id": 1}, {"chat_id": "1"}) is True
    assert nd.is_same_chat({"chat_id": 1}, {"chat_id": 2}) is False
    # fail-open toward "same" only when a side genuinely has no chat (legacy rows)
    assert nd.is_same_chat({"chat_id": None}, {"chat_id": 2}) is True
    assert nd.is_same_chat({}, {"chat_id": 2}) is True


def test_an_outbound_in_the_operator_dm_cannot_answer_a_client(monkeypatch):
    """cc-quality's finding on cosem-exams: op#37155 was marked answered by op#37169, an
    outbound in the operator DM. A different chat can never answer a client."""
    rows = [
        _r(37169, "outbound", 60, "operator note in their own DM", chat_id=int(MUSA)),
        _r(37155, "inbound", 120, "As of today may i know the list of assigned staffer"),
    ]
    w = nd.latest_unanswered_inbound(rows, musa_id=MUSA)
    assert w is not None and w["id"] == 37155, (
        "an operator-DM outbound must not silence a client message in the client's own chat"
    )


def test_a_topic_tagged_reply_in_the_client_chat_does_answer(monkeypatch):
    rows = [
        _r(37150, "outbound", 300, "One addition to point 5, on how a role gets assigned"),
        _r(37149, "outbound", 360, "Answers, all read from the live system at 07:50 UTC"),
        _r(37148, "inbound", 600, "As of today may i know the list of assigned staffer"),
    ]
    assert nd.latest_unanswered_inbound(rows, musa_id=MUSA) is None


def test_the_pin_over_an_answered_message_is_still_not_a_drop(monkeypatch):
    rows = [
        _r(37155, "inbound", 60, "[non-text update 50118302 — keys: chat,date,from,message_id,pinned_message]"),
        _r(37150, "outbound", 300, "One addition to point 5"),
        _r(37148, "inbound", 600, "As of today may i know the list of assigned staffer"),
    ]
    assert nd.latest_unanswered_inbound(rows, musa_id=MUSA) is None


def test_a_channel_with_no_resolvable_chat_still_reads(monkeypatch):
    """bariq: empty allowed_chat_ids and only operator-DM rows today. An empty chat set would
    make `chat_id = ANY('{}')` match nothing and the channel would go SILENT — so it must
    degrade to the tag-only read, exactly as before this change. A false page is recoverable;
    a silent no-drop is the failure this watcher exists to prevent."""
    results = [[], [(int(MUSA),)], []]
    _out, cur = _fetch_with(monkeypatch, results)
    msg_sql, msg_params = cur.sqls[-1], cur.params[-1]
    assert "ANY(" not in msg_sql, "empty chat set must not be sent as a ANY() filter"
    assert msg_params == ("cosem-exams", nd.FETCH_LIMIT)


# ---- per-chat judgement (cc-quality 89597 F1) -------------------------------------------
CHAT_A, CHAT_B = -5390372474, -5390372475


def test_a_quiet_sibling_chat_cannot_be_answer_shadowed():
    """THE F1 MISS. Newest inbound (chat A) is answered in chat A; chat B holds an OLDER
    unanswered client. Judging the merged newest-first list returns None for the whole
    channel and chat B is never examined."""
    rows = [
        _r(500, "outbound", 60, "reply in chat A", chat_id=CHAT_A),
        _r(499, "inbound", 120, "question in chat A", chat_id=CHAT_A),
        _r(498, "inbound", 600, "unanswered question in chat B", chat_id=CHAT_B),
    ]
    assert nd.latest_unanswered_inbound(rows, musa_id=MUSA) is None   # the old, blind answer
    got = nd.waiting_by_chat(rows, musa_id=MUSA)
    assert [w["id"] for w in got] == [498], "chat B's older unanswered client must still be seen"


def test_one_waiting_entry_per_chat_when_both_are_waiting():
    rows = [
        _r(600, "inbound", 100, "q in A", chat_id=CHAT_A),
        _r(599, "inbound", 200, "q in B", chat_id=CHAT_B),
    ]
    got = nd.waiting_by_chat(rows, musa_id=MUSA)
    assert sorted(w["id"] for w in got) == [599, 600]
    assert [w["id"] for w in got] == [600, 599], "newest-first across chats"


def test_waiting_by_chat_is_empty_when_every_chat_is_answered():
    rows = [
        _r(700, "outbound", 60, "a", chat_id=CHAT_A),
        _r(699, "inbound", 120, "q", chat_id=CHAT_A),
        _r(698, "outbound", 60, "b", chat_id=CHAT_B),
        _r(697, "inbound", 120, "q", chat_id=CHAT_B),
    ]
    assert nd.waiting_by_chat(rows, musa_id=MUSA) == []


def test_fetch_recent_limits_per_chat_not_over_the_merge(monkeypatch):
    """The read half of F1: a single LIMIT over the merged window lets a busy sibling starve a
    quiet one BEFORE any grouping can help. Each chat must get its own rows."""
    _out, cur = _fetch_with(monkeypatch, [[([CHAT_A, CHAT_B],)], []])
    sql = cur.sqls[-1]
    assert "PARTITION BY chat_id" in sql, "rows must be windowed per chat, not limited over the merge"
    assert "rn <= %s" in sql


# ---- declare-your-chat page for an undeclared channel (orch-console 89596) --------------
def test_an_undeclared_channel_with_inbound_pages_once():
    rows = [_r(800, "inbound", 300, "hello", chat_id=CHAT_A)]
    action, ns = nd.undeclared_channel_action("bariq", rows, {}, 1_000_000)
    assert action and action["to"] == "cc-fleet-health" and action["priority"] == "P3"
    assert str(CHAT_A) in action["body"] and "op#800" in action["body"]
    assert "DM" in action["body"], "must warn against declaring the operator's own DM"
    # a second run inside the window does NOT page again
    again, ns2 = nd.undeclared_channel_action("bariq", rows, ns, 1_000_100)
    assert again is None
    assert ns2[nd.UNDECLARED_KEY]["declared_paged_at"] == 1_000_000


def test_an_undeclared_channel_with_no_inbound_does_not_page():
    action, ns = nd.undeclared_channel_action("bariq", [], {}, 1_000_000)
    assert action is None and ns == {}


def test_channel_is_undeclared_reads_the_allowlist(monkeypatch):
    cur = _ChatFakeCursor([[([],)]])
    monkeypatch.setattr(nd.psycopg2, "connect", lambda *a, **k: _ChatFakeConn(cur))
    assert nd.channel_is_undeclared("postgresql://ignored", "bariq") is True
    cur2 = _ChatFakeCursor([[([CHAT_A],)]])
    monkeypatch.setattr(nd.psycopg2, "connect", lambda *a, **k: _ChatFakeConn(cur2))
    assert nd.channel_is_undeclared("postgresql://ignored", "cosem-exams") is False
