"""op#22669 operator_asks ledger: _is_operator_ask_surface (pure), maybe_track_ask
and open_asks_for (round-trip against the ephemeral operator_ledger_db fixture —
tests/conftest.py — so a schema mismatch fails the suite, never production;
orch-console bus #44006/op#22741: these tests used to run against the live
substrate via os.environ DATABASE_URL and wrote + deleted real rows there)."""
import importlib

import pytest

ol = importlib.import_module("nervous_system.operator_log")


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


# ── maybe_track_ask: round-trip against the ephemeral harness ────────────────
def test_maybe_track_ask_ignores_non_inbound_direction(operator_ledger_db):
    assert ol.maybe_track_ask(1, "outbound", "tmux-console", None, "text") is None


def test_maybe_track_ask_ignores_unmonitored_surface(operator_ledger_db):
    assert ol.maybe_track_ask(1, "inbound", "client-webhook", "cosem-tdu", "text") is None


def test_maybe_track_ask_opens_a_new_ask_on_monitored_surface(operator_ledger_db):
    import psycopg
    rid = ol.maybe_track_ask(999999, "inbound", "tmux-console", None,
                              "[__test__] op#22669 new-ask round-trip")
    assert rid is not None
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT ask, source_msg_id, closed_at FROM operator_asks WHERE id=%s", (rid,))
        ask, source_msg_id, closed_at = cur.fetchone()
    assert ask == "[__test__] op#22669 new-ask round-trip"
    assert source_msg_id == 999999
    assert closed_at is None


def test_maybe_track_ask_reply_to_our_outbound_closes_the_linked_ask(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
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

    new_id = ol.maybe_track_ask(
        888888, "inbound", "tmux-console", None, "yes, go ahead",
        reply_to_tg_message_id=424242,
    )
    assert new_id is not None and new_id != ask_id, (
        "a reply must ALSO open its own new row (op#22669 / bus #43972) — "
        "the reply's own content is never just discarded"
    )
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_at, closed_reason FROM operator_asks WHERE id=%s", (ask_id,))
        closed_at, closed_reason = cur.fetchone()
        cur.execute("SELECT ask, closed_at FROM operator_asks WHERE id=%s", (new_id,))
        new_ask, new_closed_at = cur.fetchone()
    assert closed_at is not None
    assert closed_reason == f"operator_replied op#{new_id}"
    assert new_ask == "yes, go ahead"
    assert new_closed_at is None, "the new row opened for the reply must stay OPEN"


def test_maybe_track_ask_reply_with_extra_content_leaves_one_closed_one_open(operator_ledger_db):
    """The exact op#22669 failure mode: a reply that ALSO carries new content
    ("yes, and also do X") must not silently drop that content just because it
    happened to match an open ask (orch-console bus #43972/#43985)."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered, tg_message_id) "
            "VALUES ('outbound','telegram','orch-channel','[__test__] our outbound ask 2',true,434343) "
            "RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO operator_asks (ask, outbound_msg_id) VALUES (%s,%s) RETURNING id",
            ("[__test__] waiting on a reply 2", outbound_id),
        )
        ask_id = cur.fetchone()[0]
        c.commit()

    new_id = ol.maybe_track_ask(
        898989, "inbound", "tmux-console", None,
        "yes, and also please rotate the keys",
        reply_to_tg_message_id=434343,
    )
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, closed_at FROM operator_asks WHERE id IN (%s,%s)",
            (ask_id, new_id),
        )
        by_id = dict(cur.fetchall())
    assert by_id[ask_id] is not None, "the matched ask must be closed"
    assert by_id[new_id] is None, "the reply's own row must be open"
    assert len(by_id) == 2, "exactly one closed + one open row, nothing dropped"


def test_maybe_track_ask_reply_to_unlinked_message_opens_new_ask_instead(operator_ledger_db):
    # a reply_to_tg_message_id that doesn't match any OPEN ask's outbound_msg_id
    # must fall through to opening a new ask, never silently drop the message.
    import psycopg
    rid = ol.maybe_track_ask(
        777777, "inbound", "tmux-console", None, "[__test__] reply to nothing tracked",
        reply_to_tg_message_id=13579113579,
    )
    assert rid is not None
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT ask FROM operator_asks WHERE id=%s", (rid,))
        (ask,) = cur.fetchone()
    assert ask == "[__test__] reply to nothing tracked"


# ── open_asks_for: round-trip against the ephemeral harness ─────────────────
def test_open_asks_for_returns_only_that_bodys_open_rows(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, delegated_to) VALUES "
            "('[__test__] open for orch-console','__test_body__'), "
            "('[__test__] open for someone else','__test_other_body__') "
            "RETURNING id"
        )
        ids = [r[0] for r in cur.fetchall()]
        c.commit()

    rows = ol.open_asks_for("__test_body__")
    matched = [r for r in rows if r[0] == ids[0]]
    assert len(matched) == 1
    other_ids = [r[0] for r in rows]
    assert ids[1] not in other_ids


def test_open_asks_for_excludes_closed_rows(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, delegated_to, closed_at) VALUES "
            "('[__test__] already closed','__test_body_closed__', now()) RETURNING id"
        )
        cur.fetchone()
        c.commit()

    rows = ol.open_asks_for("__test_body_closed__")
    assert rows == []


# ── log(): tg_message_id column + inbound hook wiring ────────────────────────
def test_log_persists_tg_message_id(operator_ledger_db):
    import psycopg
    rid = ol.log("outbound", "[__test__] tg_message_id round-trip", chat_id="123456",
                 tag="__test__", delivered=True, tg_message_id=555555)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT tg_message_id FROM operator_messages WHERE id=%s", (rid,))
        (tg_message_id,) = cur.fetchone()
    assert tg_message_id == 555555


def test_log_inbound_on_untracked_channel_does_not_open_an_ask(operator_ledger_db):
    # log()'s hook calls maybe_track_ask with no from_user_id, so a telegram-tag
    # row can never auto-open via log() (only ingest.py's direct call, which DOES
    # pass from_user_id, does that) -- this guards against a future regression
    # double-tracking the same inbound message via two code paths.
    import psycopg
    rid = ol.log("inbound", "[__test__] no double-track via log()", chat_id="123456",
                 tag="orch-channel", delivered=True)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM operator_asks WHERE source_msg_id=%s", (rid,))
        (n,) = cur.fetchone()
    assert n == 0


def test_log_inbound_on_tmux_console_opens_an_ask_via_the_hook(operator_ledger_db):
    import psycopg
    rid = ol.log("inbound", "[__test__] tmux-console auto-track via log()",
                 channel="tmux-console")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT id FROM operator_asks WHERE source_msg_id=%s", (rid,))
        row = cur.fetchone()
    assert row is not None, "log()'s inbound hook must open an ask for tmux-console"
