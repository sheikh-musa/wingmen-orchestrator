"""War-room live feed (WAR-ROOM-FEED-001) — OFF-LIVE verification.

These run with NO database (there is no local Postgres and we must never touch
the live substrate). The crux of the feature — cross-bot log-once and per-chat
tag routing — is exercised against an in-memory fake connection that faithfully
reproduces the two things process_update relies on:

  * ingest_dedup       — UNIQUE(channel_key, telegram_update_id), the PER-BOT
                         replay guard.
  * shared_feed_dedup  — UNIQUE(chat_id, message_id), the CROSS-BOT guard added
                         by this feature.

Both are `INSERT ... ON CONFLICT DO NOTHING RETURNING`, so a dict keyed on the
PK with "return the key iff newly inserted" is behaviourally exact for what the
code branches on. operator_messages is a plain list so we can assert exactly
which rows (and tags) were written.

Spec verification covered here (BOTH shared feeds — war-room and hafiz-partner —
which ride the identical per-chat-tag + dedup path, no per-feed code):
  1. one feed message seen by all 3 bot loops -> exactly ONE feed-tagged row,
     no DM-tag rows, no nudge (log-only; responder wired separately).
  2. both feeds are carved out of every body's PERSONAL reconciliation scope.
  3. the operator's normal DM still tags 'orch-channel' and still nudges.
"""
import pytest

pytest.importorskip("psycopg")  # ingest imports psycopg at module load

from nervous_system import ingest, operator_log  # noqa: E402

WAR_ROOM_ID = -5383530504
HAFIZ_ID = -5557014342      # Hafiz partner group — 2nd shared feed, identical path
OPERATOR_DM = 286619815


# ── In-memory fake DB: faithful ON CONFLICT semantics for the two guards ──────

class FakeCursor:
    def __init__(self, store):
        self.s = store
        self._rows = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        p = params or ()
        if "set_config" in sql:
            self._rows = None
        elif "INSERT INTO ingest_dedup" in sql:
            key = (p[0], p[1])                       # (channel_key, update_id)
            if key in self.s.ingest_dedup:
                self._rows = []                      # ON CONFLICT DO NOTHING -> no RETURNING row
            else:
                self.s.ingest_dedup[key] = None
                self._rows = [(p[0],)]
        elif "INSERT INTO shared_feed_dedup" in sql:
            key = (p[0], p[1])                       # (chat_id, message_id)
            if key in self.s.shared_feed_dedup:
                self._rows = []                      # another loop already claimed it
            else:
                self.s.shared_feed_dedup[key] = {"channel_key": p[2], "operator_msg_id": None}
                self._rows = [(p[0],)]
        elif "INSERT INTO operator_messages" in sql:
            self.s.seq += 1
            self.s.messages.append({
                "id": self.s.seq, "direction": "inbound", "channel": "telegram",
                "chat_id": p[0], "tag": p[1], "text": p[2], "handled_at": None})
            self._rows = [(self.s.seq,)]
        elif "UPDATE ingest_dedup" in sql:
            self.s.ingest_dedup[(p[1], p[2])] = p[0]
            self._rows = None
        elif "UPDATE shared_feed_dedup" in sql:
            row = self.s.shared_feed_dedup.get((p[1], p[2]))
            if row is not None:
                row["operator_msg_id"] = p[0]
            self._rows = None
        elif "SELECT count(*) FROM operator_messages" in sql:
            tag = p[0]
            n = sum(1 for m in self.s.messages
                    if m["direction"] == "inbound" and m["tag"] == tag and m["handled_at"] is None)
            self._rows = [(n,)]
        else:
            raise AssertionError(f"fake cursor: unhandled SQL: {sql[:80]!r}")

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self):
        self.ingest_dedup = {}        # (channel_key, update_id) -> operator_msg_id | None
        self.shared_feed_dedup = {}   # (chat_id, message_id) -> {...}
        self.messages = []            # operator_messages rows
        self.seq = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


# ── Channels wired as they will be POST-cutover (group_routing + allowlist) ───

def _ch(key, target, tag, group_routing):
    #        key, token_env, mode,          inject_target, prefix, responder,
    #        allowed_chat_ids,               allowed_usernames, group_routing, tag, log, offset
    row = (key, "TOK", "agent-session", target, None, None,
           [OPERATOR_DM, WAR_ROOM_ID, HAFIZ_ID], [], group_routing, tag, "substrate", None)
    return ingest.Channel(row)


# Both feeds routed on all 3 channels — exactly the post-cutover config.
_FEEDS = {"-5383530504": "war-room", "-5557014342": "hafiz-partner"}
OP_ORCH = _ch("operator-orch", "orch", "orch-channel", dict(_FEEDS))
CAI = _ch("cai-channel", "cai", "cai-channel", dict(_FEEDS))
NAZIM = _ch("nazim-console", "nazim", "nazim-console", {**_FEEDS, "nudge_when_busy": True})
ALL_LOOPS = [OP_ORCH, CAI, NAZIM]


def _upd(update_id, chat_id, message_id, text, username=None):
    return {"update_id": update_id,
            "message": {"message_id": message_id, "chat": {"id": chat_id},
                        "from": {"username": username} if username else {"id": OPERATOR_DM},
                        "text": text}}


# ── (spec §1) per-chat tag routing is a pure decision — exhaustively unit-test ─

def test_resolve_tag_routes_both_feeds_and_leaves_dm_default():
    for ch in ALL_LOOPS:
        assert ingest.resolve_tag(ch, WAR_ROOM_ID) == ("war-room", True)
        assert ingest.resolve_tag(ch, HAFIZ_ID) == ("hafiz-partner", True)
    # operator DM keeps the channel's default tag, not a shared feed
    assert ingest.resolve_tag(OP_ORCH, OPERATOR_DM) == ("orch-channel", False)
    assert ingest.resolve_tag(NAZIM, OPERATOR_DM) == ("nazim-console", False)
    # None chat / nudge_when_busy control key must never read as a route
    assert ingest.resolve_tag(OP_ORCH, None) == ("orch-channel", False)


# ── (spec §1+§2) ONE feed message, three bot loops -> ONE feed-tagged row ──────
# Parametrized over BOTH shared feeds — war-room and the Hafiz partner group ride
# the IDENTICAL per-chat-tag + cross-bot-dedup path (no per-feed code).

@pytest.mark.parametrize("feed_id, feed_tag", [
    (WAR_ROOM_ID, "war-room"),
    (HAFIZ_ID, "hafiz-partner"),
])
def test_shared_feed_logged_once_across_three_bots(monkeypatch, feed_id, feed_tag):
    nudges = []
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: nudges.append(a) or True)
    db = FakeConn()
    # The SAME logical message: identical (chat_id, message_id), a DIFFERENT
    # update_id per bot (each bot's getUpdates numbers independently).
    for i, ch in enumerate(ALL_LOOPS):
        assert ingest.process_update(db, ch, _upd(1000 + i, feed_id, 777, "fleet: status?")) is True

    assert len(db.messages) == 1                                  # logged exactly ONCE
    assert db.messages[0]["tag"] == feed_tag                      # tagged as the feed
    assert db.messages[0]["chat_id"] == str(feed_id)
    assert all(m["tag"] not in ("orch-channel", "cai-channel", "nazim-console")
               for m in db.messages)                             # NO DM-tag pollution
    assert nudges == []                                          # log-only; responder wired separately
    assert len(db.shared_feed_dedup) == 1                        # one cross-bot identity claimed
    assert len(db.ingest_dedup) == 3                             # each bot still recorded its own update

    # winner recorded its operator_msg_id on the shared guard (audit joint)
    assert db.shared_feed_dedup[(feed_id, 777)]["operator_msg_id"] == db.messages[0]["id"]

    # A Telegram REDELIVERY to the bot that already logged (same update_id) is a
    # per-bot replay -> False, no new row.
    assert ingest.process_update(db, OP_ORCH, _upd(1000, feed_id, 777, "fleet: status?")) is False
    assert len(db.messages) == 1


def test_two_feeds_are_independent_never_cross_dedup(monkeypatch):
    """A war-room msg and a Hafiz msg with the SAME message_id are distinct
    identities (different chat_id) — both log, each tagged its own feed."""
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    db = FakeConn()
    ingest.process_update(db, OP_ORCH, _upd(4000, WAR_ROOM_ID, 42, "war-room msg"))
    ingest.process_update(db, OP_ORCH, _upd(4001, HAFIZ_ID, 42, "hafiz msg"))
    assert sorted(m["tag"] for m in db.messages) == ["hafiz-partner", "war-room"]
    assert len(db.shared_feed_dedup) == 2


def test_war_room_winner_is_order_independent(monkeypatch):
    """Whichever loop arrives first logs; the tag is 'war-room' regardless."""
    monkeypatch.setattr(ingest, "nudge_session", lambda *a: True)
    for first in ALL_LOOPS:
        db = FakeConn()
        order = [first] + [c for c in ALL_LOOPS if c is not first]
        for i, ch in enumerate(order):
            ingest.process_update(db, ch, _upd(2000 + i, WAR_ROOM_ID, 555, "who owns deploy?"))
        assert len(db.messages) == 1 and db.messages[0]["tag"] == "war-room"
        assert db.shared_feed_dedup[(WAR_ROOM_ID, 555)]["channel_key"] == first.key


# ── (spec §3) operator's normal DM still tags 'orch-channel' AND still nudges ──

def test_operator_dm_unaffected_by_per_chat_routing(monkeypatch):
    nudges = []
    monkeypatch.setattr(ingest, "nudge_session",
                        lambda target, key, n: nudges.append((target, key, n)) or True)
    monkeypatch.setattr(ingest, "pane_working", lambda *a: False)   # idle -> nudge fires
    db = FakeConn()
    assert ingest.process_update(db, OP_ORCH, _upd(3000, OPERATOR_DM, 9, "deploy ihsanos")) is True
    assert len(db.messages) == 1
    assert db.messages[0]["tag"] == "orch-channel"                  # default tag, NOT re-routed
    assert len(nudges) == 1 and nudges[0][:2] == ("orch", "operator-orch")


# ── (spec §2) war-room is carved out of every body's PERSONAL reconciliation ──

def test_shared_feeds_carved_from_personal_reconciliation(monkeypatch):
    assert "war-room" in operator_log._SHARED_FEED_TAGS
    assert "hafiz-partner" in operator_log._SHARED_FEED_TAGS
    for role in ("hub", "console"):
        monkeypatch.setenv("ORCH_BODY_ROLE", role)
        scope = operator_log._channel_scope_sql()
        # unprocessed()/mark_handled exclude both feeds -> no DM pollution
        assert "war-room" in scope and "hafiz-partner" in scope
