"""Self-serve invite deep-link onboarding (Musa op#34846, orch-console #78263).

A '/start <invite-token>' inbound on a shared token BINDS the sending chat to
the invite's channel (allowed_chat_ids), consumes the single-use invite, and
replies with that channel's own self-description — no operator in the loop,
no waiting for a first 'hi'. Musa's required test list:
  1. a parallel 3-client bind (3 channels, one shared token)
  2. an invalid, replayed or expired token -> generic reply, nothing bound
  3. a cross-bind attempt (chat already bound to channel A, presents channel
     B's token) -> refused, B's invite stays usable by the right client
  4. sibling leak: the successful-bind reply carries ONLY the bound channel's
     self-description

Plus the single-use race (two chats race one invite; exactly one binds) and
the pinned/enabled invariant (a Mini-pinned channel must NOT flip enabled on
bind — that is the dual-poller 409 class — while an unpinned channel
self-activates, per op#34846).

The bind hooks INSIDE process_update AFTER the durable LOG and BEFORE the
GATE — A1/LOG/GATE/ROUTE order is untouched (the full-cycle test proves the
same update stream still logs every inbound durably and routes the
post-bind message in the same batch).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading

import psycopg
import pytest

from nervous_system import ingest
from tests.test_ingest_token_group_routing import (
    _BOT_CHANNELS_DDL,
    _INVITES_DDL,
    _SUPPORT_DDL,
    _load_group,
    _seed_channel,
)

BARIQ_DESC = ("Bariq Employment site-edit channel: send approved site changes "
              "(hero headline/description, contact details, testimonials) and "
              "I apply them to the live content, then confirm here with a "
              "preview link. Nothing else is handled on this channel.")


@pytest.fixture
def bind_dsn(pg_dsn, monkeypatch):
    """cycle_dsn's shape + the bot_channel_invites table. INGEST_DSN is pinned
    so _dsn() can never fall through to a live silo (same guard as the token
    routing tests), AND DATABASE_URL is repointed at the ephemeral cluster:
    these tests seed audience='client' channels, so process_update reaches
    operator_log.maybe_track_client_ask (3c), which resolves its own DSN from
    DATABASE_URL — on a lane shell that IS the live substrate (the exact
    #44006 write-into-production class the conftest guard documents)."""
    monkeypatch.setenv("INGEST_DSN", pg_dsn)
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("INGEST_CHANNELS", raising=False)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute(_BOT_CHANNELS_DDL)
        for ddl in _SUPPORT_DDL:  # includes bot_channel_invites (migration 126)
            cur.execute(ddl)
    return pg_dsn


def _seed_desc_channel(dsn, key, token_env_key, desc, chat_ids=()):
    """_seed_channel + a group_routing.self_description — the row the bind
    reply quotes (Refinement #4: the responder describes itself from its own
    row)."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, mode, "
            " allowed_chat_ids, allowed_usernames, group_routing, channel_tag, "
            " audience, owner_lane) VALUES (%s,%s,'log-and-route',%s,'{}', "
            "%s::jsonb, %s,'client','cc-bariq')",
            (key, token_env_key, list(chat_ids),
             json.dumps({"self_description": desc}), key))


def _seed_invite(dsn, channel_key, token, *, hours=48, used=False, expired=False):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channel_invites "
            "(channel_key, token_hash, expires_at, used_at, created_by) "
            "VALUES (%s,%s, now() + make_interval(hours => %s), %s, 'test') "
            "ON CONFLICT (token_hash) DO NOTHING RETURNING id",
            (channel_key, token_hash,
             -2 if expired else hours,
             "now()" if used else None),
        )
        return cur.fetchone()[0]


def _start_upd(update_id, chat_id, text):
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id}, "from": {"id": chat_id, "username": "afiq"},
                        "text": text}}


class _Recorder:
    """Fake tg_call: captures sendMessage payloads, answers getUpdates from a
    canned queue (same idiom as the token-group routing tests)."""

    def __init__(self, updates):
        self.updates = updates
        self.sent = []

    def __call__(self, token, method, params, timeout=None):
        if method == "getUpdates":
            upd, self.updates = self.updates, []
            return upd
        if method == "sendMessage":
            self.sent.append(params)
        return {}


def _run_cycle(dsn, token_env_key):
    channels = _load_group(dsn, token_env_key)
    asyncio.run(ingest._run_token_cycle(token_env_key, channels, 0.0))


# ── pure: hashing ────────────────────────────────────────────────────────────

def test_hash_invite_token_is_plain_sha256_hex():
    assert ingest.hash_invite_token("abc") == \
        hashlib.sha256(b"abc").hexdigest()
    assert len(ingest.hash_invite_token("abc")) == 64


# ── 1. the bind itself (full _run_token_cycle path) ─────────────────────────

def test_start_invite_binds_chat_replies_self_description(bind_dsn, monkeypatch, capsys):
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_desc_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", BARIQ_DESC)
    _seed_channel(bind_dsn, "sibling", "SHARED_BOT_TOKEN", chat_ids=[999])
    _seed_invite(bind_dsn, "bariq", "inv-token-1")

    rec = _Recorder([_start_upd(1, 424242, "/start inv-token-1")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='bariq'")
        assert cur.fetchone()[0] == [424242]
        cur.execute("SELECT used_at IS NOT NULL, bound_chat_id FROM bot_channel_invites "
                    "WHERE token_hash=%s", (hashlib.sha256(b"inv-token-1").hexdigest(),))
        assert cur.fetchone() == (True, 424242)   # consumed + audited
    assert len(rec.sent) == 1                      # exactly one reply
    assert BARIQ_DESC in rec.sent[0]["text"]       # the channel's own words
    assert rec.sent[0]["chat_id"] == 424242

    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        # inbound logged (A1+LOG) AND marked handled; reply audited outbound
        cur.execute("SELECT direction, tag, handled_at IS NOT NULL FROM operator_messages "
                    "ORDER BY id")
        rows = cur.fetchall()
        assert rows[0][0] == "inbound" and rows[0][2] is True
        assert rows[1][0] == "outbound" and rows[1][1] == "bariq"
    assert "invite-bind" in capsys.readouterr().out


def test_full_cycle_bind_then_message_routes_in_same_batch(bind_dsn, monkeypatch):
    """A1/LOG/GATE/ROUTE order proof: in ONE getUpdates batch, update 1 is the
    /start bind and update 2 is a normal message from the just-bound chat —
    both logged durably, the second ROUTES (hot in-memory allowlist update,
    no 60s config-refresh wait)."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_desc_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", BARIQ_DESC)
    _seed_invite(bind_dsn, "bariq", "inv-token-2")

    nudged = []
    rec = _Recorder([
        _start_upd(1, 424242, "/start inv-token-2"),
        _start_upd(2, 424242, "change the hero headline to 'Trusted care, every home.'"),
    ])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda tgt, key, n: nudged.append(key) or True)
    monkeypatch.setattr(ingest, "pane_working", lambda *a: False)

    channels = _load_group(bind_dsn, "SHARED_BOT_TOKEN")
    asyncio.run(ingest._run_token_cycle("SHARED_BOT_TOKEN", channels, 0.0))

    assert nudged == ["bariq"]                     # update 2 ROUTED to bariq
    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tag, text, handled_at IS NOT NULL FROM operator_messages "
                    "ORDER BY id")
        rows = cur.fetchall()
        # inbound /start (handled), outbound welcome, inbound routed message (NOT
        # handled — it is the lane's live work now)
        assert rows[0][0] == "bariq" and rows[0][2] is True
        assert rows[1][0] == "bariq" and rows[1][2] is True
        assert rows[2][0] == "bariq" and rows[2][2] is False
        assert "hero headline" in rows[2][1]


# ── 2. Musa's scenario 1: parallel 3-client bind ────────────────────────────

def test_parallel_three_clients_bind_independently(bind_dsn, monkeypatch):
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    descs = {
        "client-a": "Channel A self-description: send changes for site A.",
        "client-b": "Channel B self-description: send changes for site B.",
        "client-c": "Channel C self-description: send changes for site C.",
    }
    for key, desc in descs.items():
        with psycopg.connect(bind_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_channels (channel_key, token_env_key, mode, "
                " allowed_chat_ids, allowed_usernames, group_routing, channel_tag, "
                " audience, owner_lane) VALUES (%s,'SHARED_BOT_TOKEN','log-and-route',"
                "'{}','{}', %s::jsonb, %s,'client','coord')",
                (key, f'{{"self_description": "{desc}"}}', key))
        _seed_invite(bind_dsn, key, f"inv-{key}")

    rec = _Recorder([
        _start_upd(1, 111, "/start inv-client-a"),
        _start_upd(2, 222, "/start inv-client-b"),
        _start_upd(3, 333, "/start inv-client-c"),
    ])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT channel_key, allowed_chat_ids FROM bot_channels "
                    "ORDER BY channel_key")
        assert cur.fetchall() == [
            ("client-a", [111]), ("client-b", [222]), ("client-c", [333])]
        cur.execute("SELECT count(*) FROM bot_channel_invites WHERE used_at IS NULL")
        assert cur.fetchone()[0] == 0               # all three consumed
    assert len(rec.sent) == 3
    for params, desc in zip(rec.sent, descs.values()):
        assert desc in params["text"]               # each got ITS OWN description


# ── 3. Musa's scenario 2: invalid / replayed / expired ───────────────────────

@pytest.mark.parametrize("seed_used,seed_expired,label", [
    (False, False, "unknown-token"),
    (True, False, "replayed"),
    (False, True, "expired"),
])
def test_invalid_used_expired_token_generic_reply_nothing_bound(
        bind_dsn, monkeypatch, seed_used, seed_expired, label):
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", chat_ids=[])
    _seed_invite(bind_dsn, "bariq", "inv-token", used=seed_used, expired=seed_expired)

    text = "/start totally-wrong-token" if label == "unknown-token" else "/start inv-token"
    rec = _Recorder([_start_upd(1, 424242, text)])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    assert len(rec.sent) == 1
    assert rec.sent[0]["text"] == ingest.GENERIC_INVITE_REPLY
    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='bariq'")
        assert cur.fetchone()[0] == []              # NOTHING bound
        if label == "unknown-token":
            cur.execute("SELECT used_at IS NULL FROM bot_channel_invites")
            assert cur.fetchone()[0] is True        # invite untouched by garbage


def test_bare_start_during_live_invite_gets_generic_reply(bind_dsn, monkeypatch):
    """Client opens t.me/<bot> and taps START instead of the deep link — the
    plain '/start' (no payload) must still get the generic reply while an
    invite is outstanding, not silence."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", chat_ids=[])
    _seed_invite(bind_dsn, "bariq", "inv-token")

    rec = _Recorder([_start_upd(1, 424242, "/start")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    assert rec.sent and rec.sent[0]["text"] == ingest.GENERIC_INVITE_REPLY
    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='bariq'")
        assert cur.fetchone()[0] == []


def test_start_without_outstanding_invite_keeps_today_behavior(bind_dsn, monkeypatch, capsys):
    """No unused+unexpired invite on this token: a deep-link tap still gets
    the generic reply (op#34846: used/expired is answered, never silent), but
    a BARE '/start' (plain Start button) keeps today's behavior — logged +
    gated + handled_at, no reply, no route."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", chat_ids=[])
    _seed_invite(bind_dsn, "bariq", "inv-token", used=True)          # consumed
    _seed_invite(bind_dsn, "bariq", "inv-old", expired=True)         # stale

    rec = _Recorder([_start_upd(1, 424242, "/start inv-token"),
                     _start_upd(2, 424242, "/start")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    # only the deep-link tap is answered — with the generic reply
    assert len(rec.sent) == 1
    assert rec.sent[0]["text"] == ingest.GENERIC_INVITE_REPLY
    out = capsys.readouterr().out
    assert out.count("gated") == 1                 # only the bare /start gated
    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        # 3 rows: inbound tap, outbound generic reply, inbound bare /start —
        # all audit, nothing routed, nothing bound
        cur.execute("SELECT direction, handled_at IS NOT NULL FROM operator_messages "
                    "ORDER BY id")
        assert cur.fetchall() == [("inbound", True), ("outbound", True), ("inbound", True)]
        cur.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='bariq'")
        assert cur.fetchone()[0] == []


# ── 4. Musa's scenario 3: cross-bind attempt ─────────────────────────────────

def test_cross_bind_attempt_refused_and_invite_survives(bind_dsn, monkeypatch):
    """A chat ALREADY bound to channel-a presents channel-b's invite: refused
    with the generic reply, b stays unbound, and b's invite is NOT consumed —
    the right client can still use it."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(bind_dsn, "client-a", "SHARED_BOT_TOKEN", chat_ids=[111])
    _seed_channel(bind_dsn, "client-b", "SHARED_BOT_TOKEN", chat_ids=[])
    _seed_invite(bind_dsn, "client-b", "inv-for-b")

    rec = _Recorder([_start_upd(1, 111, "/start inv-for-b")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    assert rec.sent and rec.sent[0]["text"] == ingest.GENERIC_INVITE_REPLY
    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='client-b'")
        assert cur.fetchone()[0] == []
        cur.execute("SELECT used_at IS NULL FROM bot_channel_invites "
                    "WHERE token_hash=%s",
                    (hashlib.sha256(b"inv-for-b").hexdigest(),))
        assert cur.fetchone()[0] is True            # invite NOT burned


# ── 5. Musa's scenario 4: sibling leak ───────────────────────────────────────

def test_bind_reply_names_no_sibling(bind_dsn, monkeypatch):
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    sib_desc = "Angullia channel: send contracts for the angullia practice."
    with psycopg.connect(bind_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, mode, "
            " allowed_chat_ids, allowed_usernames, group_routing, channel_tag, "
            " audience, owner_lane) VALUES "
            "('bariq','SHARED_BOT_TOKEN','log-and-route','{}','{}', "
            "%s::jsonb, 'bariq','client','coord'), "
            "('angullia','SHARED_BOT_TOKEN','log-and-route','{777}','{}', "
            "%s::jsonb, 'angullia','client','coord')",
            (f'{{"self_description": "{BARIQ_DESC}"}}',
             f'{{"self_description": "{sib_desc}"}}'))
    _seed_invite(bind_dsn, "bariq", "inv-token")

    rec = _Recorder([_start_upd(1, 424242, "/start inv-token")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    assert len(rec.sent) == 1
    reply = rec.sent[0]["text"]
    assert BARIQ_DESC in reply
    assert "Angullia" not in reply and "angullia" not in reply
    assert "contracts" not in reply                # not even the sibling's words


# ── 6. single-use race: two chats race one invite ────────────────────────────

def test_two_chats_race_one_invite_exactly_one_binds(bind_dsn, monkeypatch):
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_desc_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", BARIQ_DESC)
    _seed_invite(bind_dsn, "bariq", "raced-token")

    ch = _load_group(bind_dsn, "SHARED_BOT_TOKEN")["bariq"]
    sent, lock, barrier = [], threading.Lock(), threading.Barrier(2)

    def fake_tg_call(token, method, params, timeout=None):
        with lock:
            sent.append(params)
        return {}

    monkeypatch.setattr(ingest, "tg_call", fake_tg_call)
    results = {}

    def racer(chat_id):
        with psycopg.connect(bind_dsn) as conn:
            barrier.wait(timeout=10)
            results[chat_id] = ingest.try_invite_bind(
                conn, ch, {"bariq": ch},
                _start_upd(900 + chat_id, chat_id, "/start raced-token")["message"],
                0)

    t1 = threading.Thread(target=racer, args=(111,))
    t2 = threading.Thread(target=racer, args=(222,))
    t1.start(), t2.start(), t1.join(30), t2.join(30)

    assert results == {111: True, 222: True}       # both fully handled: bound + refused
    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT bound_chat_id FROM bot_channel_invites "
                    "WHERE token_hash=%s",
                    (hashlib.sha256(b"raced-token").hexdigest(),))
        winner = cur.fetchone()[0]
        assert winner in (111, 222)                # exactly one winner
        cur.execute("SELECT allowed_chat_ids FROM bot_channels WHERE channel_key='bariq'")
        assert cur.fetchone()[0] == [winner]
    texts = [p["text"] for p in sent]
    assert sum(BARIQ_DESC in t or ingest.GENERIC_INVITE_REPLY in t for t in texts) == 2
    # winner got a substantive reply; loser got the generic one
    assert any(BARIQ_DESC in t for t in texts)
    assert any(t == ingest.GENERIC_INVITE_REPLY for t in texts)


# ── 7. enabled on bind: pinned stays false, unpinned self-activates ─────────

def test_pinned_channel_bind_does_not_flip_enabled(bind_dsn, monkeypatch):
    """bariq is Mini-pinned via INGEST_CHANNELS — flipping enabled=true on bind
    would dual-poll the token against the hub (the 409 class the
    pinned-drift watchdog exists for). The pin IS its liveness; the bind only
    widens the allowlist."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    monkeypatch.setenv("INGEST_CHANNELS", "bariq")   # THIS host pins bariq
    _seed_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", chat_ids=[])
    _seed_invite(bind_dsn, "bariq", "inv-token")

    rec = _Recorder([_start_upd(1, 424242, "/start inv-token")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT enabled, allowed_chat_ids FROM bot_channels "
                    "WHERE channel_key='bariq'")
        assert cur.fetchone() == (False, [424242])


def test_unpinned_channel_self_activates_on_bind(bind_dsn, monkeypatch):
    """op#34846 'the channel enables itself on successful bind' — on a host
    with no INGEST_CHANNELS pin (the hub), enabled=false means never polled,
    so the bind DOES flip it."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    monkeypatch.delenv("INGEST_CHANNELS", raising=False)
    _seed_channel(bind_dsn, "client-x", "SHARED_BOT_TOKEN", chat_ids=[])
    _seed_invite(bind_dsn, "client-x", "inv-token")

    rec = _Recorder([_start_upd(1, 424242, "/start inv-token")])
    monkeypatch.setattr(ingest, "tg_call", rec)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")

    with psycopg.connect(bind_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT enabled, allowed_chat_ids FROM bot_channels "
                    "WHERE channel_key='client-x'")
        assert cur.fetchone() == (True, [424242])


# ── 5c. empty-allowlist watchdog vs the invite-pending window (fast-follow b) ─
# green-lit by console-revamp #78815: the watchdog skip for channels whose
# empty allowlist is the DESIGNED pre-bind state.

def test_empty_allowlist_watchdog_silent_while_invite_pending(bind_dsn, monkeypatch):
    """op#34846: an empty allowlist is the designed pre-bind state while an
    unused, unexpired invite is outstanding — the pre-GATE bind hook answers
    the '/start <token>' tap, so the watchdog's "never match any message"
    claim is stale for exactly that window and must not fire (the ~29s log
    noise class on the Mini until the client taps)."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_desc_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", BARIQ_DESC)
    _seed_channel(bind_dsn, "sibling", "SHARED_BOT_TOKEN", chat_ids=[999])
    _seed_invite(bind_dsn, "bariq", "tok-pending", hours=48)
    captured = []
    monkeypatch.setattr(ingest, "_log_line", lambda msg: captured.append(msg))
    monkeypatch.setattr(ingest, "tg_call", _Recorder([]))
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")
    assert not [m for m in captured if "EMPTY allowed_chat_ids" in m and "'bariq'" in m]


def test_empty_allowlist_watchdog_fires_without_invite(bind_dsn, monkeypatch):
    """The guard still guards: an empty-allowlist channel with NO outstanding
    invite genuinely matches nothing — the watchdog must keep saying so."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_desc_channel(bind_dsn, "ghost", "SHARED_BOT_TOKEN", "Ghost channel")
    _seed_channel(bind_dsn, "sibling", "SHARED_BOT_TOKEN", chat_ids=[999])
    captured = []
    monkeypatch.setattr(ingest, "_log_line", lambda msg: captured.append(msg))
    monkeypatch.setattr(ingest, "tg_call", _Recorder([]))
    _run_cycle(bind_dsn, "SHARED_BOT_TOKEN")
    assert [m for m in captured if "EMPTY allowed_chat_ids" in m and "'ghost'" in m]


def test_invite_pending_window_is_unused_and_unexpired(bind_dsn):
    """The skip window is exactly: an unused AND unexpired invite — used or
    expired invites don't suppress the watchdog."""
    _seed_desc_channel(bind_dsn, "bariq", "SHARED_BOT_TOKEN", BARIQ_DESC)
    _seed_invite(bind_dsn, "bariq", "tok-used", used=True)
    _seed_invite(bind_dsn, "bariq", "tok-expired", expired=True)
    with psycopg.connect(bind_dsn) as conn:
        assert not ingest.invite_pending(conn, "bariq")
    _seed_invite(bind_dsn, "bariq", "tok-live", hours=48)
    with psycopg.connect(bind_dsn) as conn:
        assert ingest.invite_pending(conn, "bariq")
