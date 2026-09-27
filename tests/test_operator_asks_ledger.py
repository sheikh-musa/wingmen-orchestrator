"""op#22669 operator_asks ledger: _is_operator_ask_surface (pure), maybe_track_ask
and open_asks_for (live-DB round-trip, same convention as
test_operator_log_failure_reason.py — mock the network, exercise the REAL
tables so a schema mismatch fails the suite, not production; every inserted
row is tagged/cleaned up)."""
import importlib
import os

import pytest

ol = importlib.import_module("nervous_system.operator_log")


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


# ── _is_operator_ask_surface: pure logic, no DB ───────────────────────────────
def test_tmux_console_is_always_a_monitored_surface():
    # operator-only by construction (only log_console_msg.sh writes it) — no
    # from_user_id to check, and none is required.
    assert ol._is_operator_ask_surface("tmux-console", None, None) is True
    assert ol._is_operator_ask_surface("tmux-console", "anything", None) is True


def test_telegram_orch_channel_from_musa_is_monitored(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", "999")
    assert ol._is_operator_ask_surface("telegram", "orch-channel", "999") is True


def test_telegram_nazim_console_from_musa_is_monitored(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", "999")
    assert ol._is_operator_ask_surface("telegram", "nazim-console", "999") is True


def test_telegram_tracked_tag_from_a_different_sender_is_not_monitored(monkeypatch):
    # the tag alone doesn't prove who sent it — a bot-channel message from
    # someone other than Musa must not be tracked as an operator ask.
    monkeypatch.setenv("MUSA_TELEGRAM_ID", "999")
    assert ol._is_operator_ask_surface("telegram", "orch-channel", "111") is False


def test_telegram_tracked_tag_with_no_from_user_id_is_not_monitored(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", "999")
    assert ol._is_operator_ask_surface("telegram", "orch-channel", None) is False


def test_telegram_untracked_tag_is_never_monitored(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", "999")
    assert ol._is_operator_ask_surface("telegram", "cosem-tdu", "999") is False


def test_telegram_with_no_musa_id_configured_fails_closed(monkeypatch):
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    assert ol._is_operator_ask_surface("telegram", "orch-channel", "999") is False


def test_unknown_channel_is_never_monitored():
    assert ol._is_operator_ask_surface("client-webhook", "orch-channel", "999") is False


# ── maybe_track_ask: live round-trip ─────────────────────────────────────────
@pytest.mark.skipif(not _dsn(), reason="no DSN (offline CI) — the pure surface tests cover logic")
def test_maybe_track_ask_ignores_non_inbound_direction():
    assert ol.maybe_track_ask(1, "outbound", "tmux-console", None, "text") is None


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_maybe_track_ask_ignores_unmonitored_surface():
    assert ol.maybe_track_ask(1, "inbound", "client-webhook", "cosem-tdu", "text") is None


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_maybe_track_ask_opens_a_new_ask_on_monitored_surface():
    import psycopg
    rid = ol.maybe_track_ask(999999, "inbound", "tmux-console", None,
                              "[__test__] op#22669 new-ask round-trip")
    assert rid is not None
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT ask, source_msg_id, closed_at FROM operator_asks WHERE id=%s", (rid,))
            ask, source_msg_id, closed_at = cur.fetchone()
        assert ask == "[__test__] op#22669 new-ask round-trip"
        assert source_msg_id == 999999
        assert closed_at is None
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_maybe_track_ask_reply_to_our_outbound_closes_the_linked_ask():
    import psycopg
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered, tg_message_id) "
            "VALUES ('outbound','telegram','orch-channel','[__test__] our outbound ask',true,424242) "
            "RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO operator_asks (ask, outbound_msg_id) VALUES (%s,%s) RETURNING id",
            ("[__test__] waiting on a reply", outbound_id),
        )
        ask_id = cur.fetchone()[0]
        c.commit()
    try:
        rid = ol.maybe_track_ask(
            888888, "inbound", "tmux-console", None, "yes, go ahead",
            reply_to_tg_message_id=424242,
        )
        assert rid == ask_id, "a genuine reply to the linked outbound must close THAT ask, not open a new one"
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT closed_at, closed_reason FROM operator_asks WHERE id=%s", (ask_id,))
            closed_at, closed_reason = cur.fetchone()
        assert closed_at is not None
        assert closed_reason == "operator_replied"
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (ask_id,))
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (outbound_id,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_maybe_track_ask_reply_to_unlinked_message_opens_new_ask_instead():
    # a reply_to_tg_message_id that doesn't match any OPEN ask's outbound_msg_id
    # must fall through to opening a new ask, never silently drop the message.
    rid = ol.maybe_track_ask(
        777777, "inbound", "tmux-console", None, "[__test__] reply to nothing tracked",
        reply_to_tg_message_id=13579113579,
    )
    assert rid is not None
    import psycopg
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT ask FROM operator_asks WHERE id=%s", (rid,))
            (ask,) = cur.fetchone()
        assert ask == "[__test__] reply to nothing tracked"
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()


# ── open_asks_for: live round-trip ───────────────────────────────────────────
@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_open_asks_for_returns_only_that_bodys_open_rows():
    import psycopg
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, delegated_to) VALUES "
            "('[__test__] open for orch-console','__test_body__'), "
            "('[__test__] open for someone else','__test_other_body__') "
            "RETURNING id"
        )
        ids = [r[0] for r in cur.fetchall()]
        c.commit()
    try:
        rows = ol.open_asks_for("__test_body__")
        matched = [r for r in rows if r[0] == ids[0]]
        assert len(matched) == 1
        other_ids = [r[0] for r in rows]
        assert ids[1] not in other_ids
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id = ANY(%s)", (ids,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_open_asks_for_excludes_closed_rows():
    import psycopg
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, delegated_to, closed_at) VALUES "
            "('[__test__] already closed','__test_body_closed__', now()) RETURNING id"
        )
        rid = cur.fetchone()[0]
        c.commit()
    try:
        rows = ol.open_asks_for("__test_body_closed__")
        assert rows == []
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()


# ── log(): tg_message_id column + inbound hook wiring ────────────────────────
@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_log_persists_tg_message_id():
    import psycopg
    rid = ol.log("outbound", "[__test__] tg_message_id round-trip", chat_id="123456",
                 tag="__test__", delivered=True, tg_message_id=555555)
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT tg_message_id FROM operator_messages WHERE id=%s", (rid,))
            (tg_message_id,) = cur.fetchone()
        assert tg_message_id == 555555
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (rid,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_log_inbound_on_untracked_channel_does_not_open_an_ask():
    # log()'s hook calls maybe_track_ask with no from_user_id, so a telegram-tag
    # row can never auto-open via log() (only ingest.py's direct call, which DOES
    # pass from_user_id, does that) -- this guards against a future regression
    # double-tracking the same inbound message via two code paths.
    import psycopg
    rid = ol.log("inbound", "[__test__] no double-track via log()", chat_id="123456",
                 tag="orch-channel", delivered=True)
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM operator_asks WHERE source_msg_id=%s", (rid,))
            (n,) = cur.fetchone()
        assert n == 0
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (rid,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_log_inbound_on_tmux_console_opens_an_ask_via_the_hook():
    import psycopg
    rid = ol.log("inbound", "[__test__] tmux-console auto-track via log()",
                 channel="tmux-console")
    ask_id = None
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT id FROM operator_asks WHERE source_msg_id=%s", (rid,))
            row = cur.fetchone()
        assert row is not None, "log()'s inbound hook must open an ask for tmux-console"
        ask_id = row[0]
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            if ask_id is not None:
                cur.execute("DELETE FROM operator_asks WHERE id=%s", (ask_id,))
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (rid,))
            c.commit()
