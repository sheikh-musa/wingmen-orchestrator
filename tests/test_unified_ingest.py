"""Unified bot ingest (CAI-RESP-357) — offline integration tests.

Runs against an EPHEMERAL DB with migration 014 applied (never prod):

    INGEST_DSN=postgresql://... pytest tests/test_unified_ingest.py

Skips cleanly when INGEST_DSN is unset. Covers the amendment obligations:
  A1 — a redelivered update never double-logs (dedupe is the processing gate)
  gate — deny-by-default (empty allowlists accept nothing; chat-id and
         scoped-username paths both honoured)
  A2 — responder_runner drains the log via registered handlers only; missing
       handler leaves rows queued (nothing consumed by a brain that isn't there)
  tg_out — chunking honours the 4096 limit; failed delivery walks
           failed→dead, never silently dropped
"""
import os

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("INGEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="INGEST_DSN not set (ephemeral only)")

from nervous_system import ingest, responder_runner, tg_out  # noqa: E402


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DSN, autocommit=False) as conn:
        with conn.cursor() as cur:
            # minimal operator_messages for the ephemeral (monolith owns the real one)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operator_messages (
                  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                  direction TEXT NOT NULL, channel TEXT, chat_id TEXT, tag TEXT,
                  text TEXT NOT NULL, delivered BOOLEAN NOT NULL DEFAULT true,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), handled_at TIMESTAMPTZ)
            """)
            cur.execute("TRUNCATE ingest_dedup, tg_out, operator_messages")
            cur.execute("""
                UPDATE bot_channels
                   SET enabled=true, allowed_chat_ids='{1111}',
                       allowed_usernames='{desmond_shen}'
                 WHERE channel_key='operator-orch'
            """)
        conn.commit()
        yield conn
        conn.rollback()


def _channel(db, key="operator-orch"):
    with db.cursor() as cur:
        cur.execute(f"SELECT {ingest.Channel.COLS} FROM bot_channels WHERE channel_key=%s", (key,))
        return ingest.Channel(cur.fetchone())


def _upd(update_id, chat_id=1111, text="hello", username=None):
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id},
                        "from": {"username": username} if username else {},
                        "text": text}}


# ── A1: idempotent ingest ─────────────────────────────────────────────────────

def test_redelivered_update_never_double_logs(db, monkeypatch):
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    ch = _channel(db)
    assert ingest.process_update(db, ch, _upd(9001)) is True      # first delivery wins
    assert ingest.process_update(db, ch, _upd(9001)) is False     # replay loses
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM operator_messages WHERE text='hello'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT operator_msg_id FROM ingest_dedup WHERE telegram_update_id=9001")
        assert cur.fetchone()[0] is not None                      # audit joint set


# ── Gate: deny-by-default from config ─────────────────────────────────────────

def test_gate_deny_by_default_and_allow_paths(db):
    ch = _channel(db)
    assert ingest.gate_allows(ch, 1111, None) is True             # allow-listed chat
    assert ingest.gate_allows(ch, 2222, None) is False            # unknown chat
    assert ingest.gate_allows(ch, 2222, "desmond_shen") is True   # scoped partner (Shen)
    assert ingest.gate_allows(ch, 2222, "@Desmond_Shen") is True  # case/@ insensitive
    assert ingest.gate_allows(ch, 2222, "stranger") is False
    ch.allowed_chat_ids, ch.allowed_usernames = [], []
    assert ingest.gate_allows(ch, 1111, "desmond_shen") is False  # empty = accept NOTHING


def test_gated_update_is_logged_but_not_routed(db, monkeypatch):
    nudges = []
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: nudges.append(a))
    ch = _channel(db)
    ingest.process_update(db, ch, _upd(9002, chat_id=5555, text="spam"))
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM operator_messages WHERE text='spam'")
        assert cur.fetchone()[0] == 1                             # audit kept
    assert nudges == []                                           # no routing


def test_allowed_update_nudges_count_only(db, monkeypatch):
    nudges = []
    monkeypatch.setattr(ingest, "nudge_session",
                        lambda target, key, n: nudges.append((target, key, n)) or True)
    ch = _channel(db)
    ingest.process_update(db, ch, _upd(9003, text="payload NEVER injected"))
    assert len(nudges) == 1
    target, key, n = nudges[0]
    assert (target, key) == ("orch", "operator-orch") and n >= 1  # count, not payload


# ── A2: responder drain ───────────────────────────────────────────────────────

def test_missing_handler_leaves_rows_queued(db):
    with db.cursor() as cur:
        cur.execute("""UPDATE bot_channels SET enabled=true, allowed_chat_ids='{7777}'
                       WHERE channel_key='mamadah'""")
        cur.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, text)
                       VALUES ('inbound','telegram','7777','mamadah','what did i note about tajweed?')""")
    db.commit()
    responder_runner.HANDLERS.pop("mamadah_second_brain", None)
    assert responder_runner.drain_once(db) == 0
    with db.cursor() as cur:
        cur.execute("SELECT handled_at FROM operator_messages WHERE tag='mamadah'")
        assert cur.fetchone()[0] is None                          # untouched, still queued


def test_registered_handler_replies_and_stamps(db, monkeypatch):
    monkeypatch.setattr(tg_out, "_dsn", lambda: DSN)
    responder_runner.HANDLERS["mamadah_second_brain"] = \
        lambda text, chat_id, conn: f"echo: {text}"
    try:
        assert responder_runner.drain_once(db) == 1
        with db.cursor() as cur:
            cur.execute("SELECT handled_at FROM operator_messages WHERE tag='mamadah'")
            assert cur.fetchone()[0] is not None                  # stamped (Option B)
            cur.execute("SELECT channel_key, chat_id, text, status FROM tg_out")
            key, chat, text, status = cur.fetchone()
            assert (key, chat, status) == ("mamadah", 7777, "queued")
            assert text.startswith("echo:")                      # reply enqueued, not sent inline
    finally:
        responder_runner.HANDLERS.pop("mamadah_second_brain", None)


# ── tg_out: chunking + failure walk ───────────────────────────────────────────

def test_chunking_honours_telegram_limit():
    parts = tg_out.chunk_text("word " * 2000)                     # ~10k chars
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(p if i == len(parts) - 1 else p + " "
                   for i, p in enumerate(parts)).split() == ("word " * 2000).split()
    assert tg_out.chunk_text("short") == ["short"]


def test_failed_delivery_walks_to_dead_never_dropped(db, monkeypatch):
    monkeypatch.setattr(tg_out, "_dsn", lambda: DSN)
    rid = tg_out.enqueue("operator-orch", "will fail")
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(tg_out, "_send_text", boom)
    monkeypatch.setattr(tg_out, "RETRY_BACKOFF", 0)
    for _ in range(tg_out.MAX_ATTEMPTS + 1):
        tg_out.drain_once(db)
    with db.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM tg_out WHERE id=%s", (rid,))
        status, attempts, err = cur.fetchone()
    assert status == "dead" and attempts == tg_out.MAX_ATTEMPTS   # watchdog surface
    assert "network down" in err
