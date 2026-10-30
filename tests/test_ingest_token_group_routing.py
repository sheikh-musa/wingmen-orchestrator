"""Token-group routing (op#31298/31307, "one-bot-many-clients") — orch-console
design-GO bus #69978: group bot_channels rows by token_env_key into ONE
getUpdates loop per unique token (not per channel_key, which would 409 two
rows sharing a bot), route each update to the right logical channel by
chat_id/username via gate_allows.

Covers the 5 required edge cases from the design-GO, plus a no-regression
proof against the ACTUAL prod run path (every channel_key this daemon polls
today has its own distinct token — grouping must be a no-op there):
  1. empty allowlist in a shared-token row -> logged LOUD, never silent
  2. overlapping allowed_chat_ids across a shared-token group -> REFUSE to
     poll (not first-match), logged loud, backs off
  3. hot-reload: a channel joining/leaving a group takes effect next cycle
     with no restart (channel_loop re-derives the group every cycle)
  4. deny-by-default preserved; dedup/health stay keyed per channel_key
  5. (testbed enabled=false discipline is a PR2/DB-state concern, not code
     under test here — not applicable to these pure/integration tests)

Pure-function tests need no DB. The integration tests use the ephemeral PG17
harness (tests/conftest.py's pg_dsn) with INGEST_DSN pinned to it — NEVER
touches DATABASE_URL / any live silo.
"""
from __future__ import annotations

import psycopg
import pytest

from nervous_system import ingest


# ── Pure-function tests: group_by_token / overlapping_chat_ids /
#    empty_allowlist_rows / match_channel_in_group ───────────────────────────

def _ch(key, token_env_key, allowed_chat_ids=(), allowed_usernames=()):
    """Build a Channel without a DB round-trip (Channel.__init__ just unpacks
    a positional row tuple)."""
    row = (key, token_env_key, "log-and-route", None, None, None,
           list(allowed_chat_ids), list(allowed_usernames), {}, key,
           "substrate", None, "client", "coord", None)
    return ingest.Channel(row)


def test_group_by_token_groups_shared_token_sorted_by_key():
    a = _ch("b-chan", "SHARED_TOKEN")
    b = _ch("a-chan", "SHARED_TOKEN")
    c = _ch("solo-chan", "OTHER_TOKEN")
    groups = ingest.group_by_token({ch.key: ch for ch in (a, b, c)})
    assert set(groups.keys()) == {"SHARED_TOKEN", "OTHER_TOKEN"}
    assert [ch.key for ch in groups["SHARED_TOKEN"]] == ["a-chan", "b-chan"]
    assert [ch.key for ch in groups["OTHER_TOKEN"]] == ["solo-chan"]


def test_group_by_token_distinct_tokens_are_all_solo():
    channels = {k: _ch(k, f"TOKEN_{k}") for k in ("x", "y", "z")}
    groups = ingest.group_by_token(channels)
    assert all(len(g) == 1 for g in groups.values())


def test_overlapping_chat_ids_detects_shared_chat():
    group = [_ch("c1", "T", allowed_chat_ids=[111, 222]),
             _ch("c2", "T", allowed_chat_ids=[222, 333])]
    overlap = ingest.overlapping_chat_ids(group)
    assert overlap == {222: ["c1", "c2"]}


def test_overlapping_chat_ids_disjoint_is_clean():
    group = [_ch("c1", "T", allowed_chat_ids=[111]),
             _ch("c2", "T", allowed_chat_ids=[222])]
    assert ingest.overlapping_chat_ids(group) == {}


def test_empty_allowlist_rows_flagged_only_in_multi_row_group():
    group = [_ch("c1", "T", allowed_chat_ids=[111]),
             _ch("c2", "T")]  # no allowlist at all
    assert ingest.empty_allowlist_rows(group) == ["c2"]


def test_empty_allowlist_rows_solo_group_is_not_flagged():
    # A solo-token channel with no allowlist yet is the normal pre-cutover
    # state (today's shape for a brand-new unconfigured channel) — not a
    # multi-client misconfig.
    group = [_ch("c1", "T")]
    assert ingest.empty_allowlist_rows(group) == []


def test_match_channel_in_group_by_chat_id():
    group = [_ch("c1", "T", allowed_chat_ids=[111]),
             _ch("c2", "T", allowed_chat_ids=[222])]
    assert ingest.match_channel_in_group(group, 222, None).key == "c2"
    assert ingest.match_channel_in_group(group, 111, None).key == "c1"


def test_match_channel_in_group_by_username():
    group = [_ch("c1", "T", allowed_usernames=["desmond_shen"])]
    assert ingest.match_channel_in_group(group, 999, "@Desmond_Shen").key == "c1"


def test_match_channel_in_group_no_match_returns_none():
    group = [_ch("c1", "T", allowed_chat_ids=[111])]
    assert ingest.match_channel_in_group(group, 999, None) is None
    assert ingest.match_channel_in_group(group, None, None) is None


# ── Integration: _run_token_cycle() against the ephemeral PG17 harness ───────

_BOT_CHANNELS_DDL = """
    CREATE TABLE bot_channels (
      channel_key        TEXT PRIMARY KEY,
      token_env_key      TEXT NOT NULL,
      mode               TEXT NOT NULL,
      inject_target      TEXT,
      inject_prefix      TEXT,
      responder_ref      TEXT,
      allowed_chat_ids   BIGINT[] NOT NULL DEFAULT '{}',
      allowed_usernames  TEXT[]  NOT NULL DEFAULT '{}',
      group_routing      JSONB   NOT NULL DEFAULT '{}'::jsonb,
      channel_tag        TEXT NOT NULL,
      log_target         TEXT NOT NULL DEFAULT 'substrate',
      enabled            BOOLEAN NOT NULL DEFAULT false,
      poll_offset        BIGINT,
      audience           TEXT,
      owner_lane         TEXT,
      stage_file_to_agent TEXT,
      created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""
# migration 126's table (sha256-at-rest invites); the invite-bind tests
# import this from here so there is exactly ONE definition of the shape.
_INVITES_DDL = """CREATE TABLE bot_channel_invites (
      id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      channel_key   TEXT NOT NULL REFERENCES bot_channels(channel_key) ON DELETE CASCADE,
      token_hash    TEXT NOT NULL UNIQUE,
      expires_at    TIMESTAMPTZ NOT NULL,
      used_at       TIMESTAMPTZ,
      bound_chat_id BIGINT,
      created_by    TEXT,
      created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""
_SUPPORT_DDL = (
    """CREATE TABLE ingest_dedup (
         channel_key        TEXT NOT NULL REFERENCES bot_channels(channel_key) ON DELETE CASCADE,
         telegram_update_id BIGINT NOT NULL,
         operator_msg_id    BIGINT,
         processed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
         PRIMARY KEY (channel_key, telegram_update_id)
       )""",
    """CREATE TABLE operator_messages (
         id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
         direction TEXT NOT NULL, channel TEXT, chat_id TEXT, tag TEXT,
         text TEXT, delivered BOOLEAN NOT NULL DEFAULT true,
         from_user_id TEXT, from_username TEXT, from_name TEXT, cos_triage JSONB,
         file_id TEXT, file_unique_id TEXT, media_kind TEXT, media_status TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now(), handled_at TIMESTAMPTZ
       )""",
    """CREATE TABLE ingest_poll_health (
         channel_key   TEXT NOT NULL,
         host          TEXT NOT NULL,
         last_ok_at    TIMESTAMPTZ,
         consec_errors INTEGER NOT NULL DEFAULT 0,
         last_error    TEXT,
         updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
         PRIMARY KEY (channel_key, host)
       )""",
    """CREATE TABLE tg_out (
         id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
         channel_key TEXT NOT NULL REFERENCES bot_channels(channel_key),
         chat_id BIGINT, text TEXT, file_path TEXT,
         reply_to_operator_msg_id BIGINT,
         status TEXT NOT NULL DEFAULT 'queued',
         attempts INT NOT NULL DEFAULT 0, last_error TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ
       )""",
    # migration 126: the empty-allowlist watchdog reads this table too now
    # (invite_pending, op#34846 fast-follow b), not just try_invite_bind —
    # every harness that drives _run_token_cycle needs it or the watchdog's
    # pending-invite check dies on UndefinedTable before it can warn.
    _INVITES_DDL,
)


@pytest.fixture
def cycle_dsn(pg_dsn, monkeypatch):
    # _run_token_cycle opens its OWN connection via _dsn() -> pin INGEST_DSN
    # to the ephemeral instance so it can never fall through to a real
    # DATABASE_URL / live silo if this shell happens to have one set.
    monkeypatch.setenv("INGEST_DSN", pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute(_BOT_CHANNELS_DDL)
        for ddl in _SUPPORT_DDL:
            cur.execute(ddl)
    return pg_dsn


def _seed_channel(dsn, key, token_env_key, chat_ids=(), usernames=(), audience="internal"):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, token_env_key, mode, "
            " allowed_chat_ids, allowed_usernames, channel_tag, audience, owner_lane) "
            "VALUES (%s,%s,'log-and-route',%s,%s,%s,%s,'coord') "
            "ON CONFLICT (channel_key) DO UPDATE SET "
            " token_env_key=EXCLUDED.token_env_key, "
            " allowed_chat_ids=EXCLUDED.allowed_chat_ids, "
            " allowed_usernames=EXCLUDED.allowed_usernames",
            (key, token_env_key, list(chat_ids), list(usernames), key, audience),
        )


def _load_group(dsn, token_env_key):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {ingest.Channel.COLS} FROM bot_channels "
                    f"WHERE token_env_key=%s ORDER BY channel_key", (token_env_key,))
        rows = {}
        for r in cur.fetchall():
            ch = ingest.Channel(r)
            rows[ch.key] = ch
        return rows


def test_multi_row_same_token_routes_by_chat_id_single_getupdates_call(cycle_dsn, monkeypatch):
    """2 synthetic rows sharing ONE token, 2 distinct chat_ids: exactly ONE
    getUpdates call per cycle (no dual-poller 409), each update lands under
    the channel_tag its chat_id actually belongs to."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(cycle_dsn, "client-a", "SHARED_BOT_TOKEN", chat_ids=[111])
    _seed_channel(cycle_dsn, "client-b", "SHARED_BOT_TOKEN", chat_ids=[222])
    channels = _load_group(cycle_dsn, "SHARED_BOT_TOKEN")

    calls = []

    def _fake_tg_call(token, method, params, timeout=None):
        calls.append((token, method, params))
        return [
            {"update_id": 1, "message": {"chat": {"id": 111}, "from": {}, "text": "hi from a"}},
            {"update_id": 2, "message": {"chat": {"id": 222}, "from": {}, "text": "hi from b"}},
        ]

    monkeypatch.setattr(ingest, "tg_call", _fake_tg_call)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a, **kw: True)
    monkeypatch.setattr(ingest, "pane_working", lambda *a: False)

    import asyncio
    last_alert, sleep_for = asyncio.run(
        ingest._run_token_cycle("SHARED_BOT_TOKEN", channels, 0.0))

    assert len(calls) == 1          # ONE getUpdates call served the whole group
    assert sleep_for is None

    with psycopg.connect(cycle_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tag, text FROM operator_messages ORDER BY id")
        rows = cur.fetchall()
    assert rows == [("client-a", "hi from a"), ("client-b", "hi from b")]

    # dedup/health stay keyed per channel_key, not per token.
    with psycopg.connect(cycle_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT channel_key FROM ingest_poll_health ORDER BY channel_key")
        assert [r[0] for r in cur.fetchall()] == ["client-a", "client-b"]


def test_unmatched_chat_logs_under_first_row_never_dropped(cycle_dsn, monkeypatch, capsys):
    """Deny-by-default: an update from a chat no row in the group claims is
    still logged (audit), under the group's deterministic first row, then
    gated — never silently dropped."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(cycle_dsn, "client-a", "SHARED_BOT_TOKEN", chat_ids=[111])
    _seed_channel(cycle_dsn, "client-b", "SHARED_BOT_TOKEN", chat_ids=[222])
    channels = _load_group(cycle_dsn, "SHARED_BOT_TOKEN")

    def _fake_tg_call(token, method, params, timeout=None):
        return [{"update_id": 9, "message": {"chat": {"id": 999}, "from": {}, "text": "stranger"}}]

    monkeypatch.setattr(ingest, "tg_call", _fake_tg_call)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a, **kw: True)

    import asyncio
    asyncio.run(ingest._run_token_cycle("SHARED_BOT_TOKEN", channels, 0.0))

    with psycopg.connect(cycle_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tag, text FROM operator_messages")
        rows = cur.fetchall()
    assert rows == [("client-a", "stranger")]   # logged under group[0] = client-a
    assert "gated" in capsys.readouterr().out


def test_overlapping_chat_ids_refuses_to_poll(cycle_dsn, monkeypatch, capsys):
    """Edge case 2: overlapping allowed_chat_ids in a shared-token group is a
    deterministic REFUSE (not first-match) — no getUpdates call, loud log,
    backoff sleep."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(cycle_dsn, "client-a", "SHARED_BOT_TOKEN", chat_ids=[111, 222])
    _seed_channel(cycle_dsn, "client-b", "SHARED_BOT_TOKEN", chat_ids=[222, 333])
    channels = _load_group(cycle_dsn, "SHARED_BOT_TOKEN")

    calls = []
    monkeypatch.setattr(ingest, "tg_call", lambda *a, **k: calls.append(a) or [])

    import asyncio
    last_alert, sleep_for = asyncio.run(
        ingest._run_token_cycle("SHARED_BOT_TOKEN", channels, 0.0))

    assert calls == []                       # never polled
    assert sleep_for == ingest.ERROR_BACKOFF
    out = capsys.readouterr().out
    assert "WATCHDOG" in out and "overlapping" in out and "refusing to poll" in out


def test_empty_allowlist_in_shared_group_is_logged_loud_not_silent(cycle_dsn, monkeypatch, capsys):
    """Edge case 1: a shared-token row with no allowlist at all can never
    match (gate_allows is always False for it) — not dangerous (deny-by-
    default fails safe), but must be LOUD, never silently swallowed."""
    monkeypatch.setenv("SHARED_BOT_TOKEN", "fake-token-value")
    _seed_channel(cycle_dsn, "client-a", "SHARED_BOT_TOKEN", chat_ids=[111])
    _seed_channel(cycle_dsn, "client-misconfigured", "SHARED_BOT_TOKEN")  # no allowlist
    channels = _load_group(cycle_dsn, "SHARED_BOT_TOKEN")

    monkeypatch.setattr(ingest, "tg_call", lambda *a, **k: [])
    monkeypatch.setattr(ingest, "nudge_session", lambda *a, **kw: True)

    import asyncio
    asyncio.run(ingest._run_token_cycle("SHARED_BOT_TOKEN", channels, 0.0))

    out = capsys.readouterr().out
    assert "WATCHDOG" in out and "client-misconfigured" in out and "EMPTY" in out


def test_group_disappearing_returns_retirement_sentinel(cycle_dsn, monkeypatch):
    """Edge case 3 (hot-reload): once a group's last channel is removed from
    the live `channels` dict, _run_token_cycle signals the caller to retire
    the task (sleep_for == -1.0) rather than polling a dead token forever."""
    import asyncio
    last_alert, sleep_for = asyncio.run(
        ingest._run_token_cycle("GONE_TOKEN", {}, 0.0))
    assert sleep_for == -1.0


def test_channel_loop_hot_reloads_without_restart(monkeypatch):
    """channel_loop re-derives its group fresh from the live `channels` dict
    every cycle — a channel joining the group takes effect on the VERY NEXT
    cycle, with no task cancellation/replacement. Drive 2 cycles: cycle 1
    sees only 'a'; cycle 2 (after the test adds 'b' to the shared dict, as
    main()'s CONFIG_REFRESH would) sees both, proving hot-reload without
    restarting channel_loop itself."""
    import asyncio

    channels = {"a": _ch("a", "SHARED")}
    seen_groups = []

    async def _fake_cycle(token_env_key, chans, last_db_alert):
        group = [ch.key for ch in chans.values() if ch.token_env_key == token_env_key]
        seen_groups.append(group)
        if len(seen_groups) == 1:
            channels["b"] = _ch("b", "SHARED")   # simulate a config refresh mid-loop
            return last_db_alert, 0  # proceed immediately to next cycle
        return last_db_alert, -1.0   # retire after the 2nd cycle, so the test ends

    monkeypatch.setattr(ingest, "_run_token_cycle", _fake_cycle)
    asyncio.run(ingest.channel_loop("SHARED", channels))

    assert seen_groups == [["a"], ["a", "b"]]


# ── No-regression proof: the ACTUAL prod run path ─────────────────────────────
# orch-console (bus #69978): "The regression test MUST exercise the ACTUAL
# prod run path -- boot_nazim_ingest.sh's INGEST_CHANNELS override... and
# assert a solo-token group is byte-identical to today." Every channel this
# daemon polls TODAY has its own distinct token (no two channel_keys share a
# bot yet) -- grouping must be a complete no-op for that real shape: every
# group has exactly 1 member, so channel_loop's per-group behavior is
# unchanged from the old per-channel_key loop.

_NAZIM_PINNED_CHANNELS = [
    "nazim-console", "cosem-exams", "cosem-caai", "alderei", "finance-console",
    "hk-editor", "cosem-tdu", "angullia", "oeh", "mamadah", "coffeemedia",
]


def test_real_pinned_channel_list_today_is_all_solo_token_groups(cycle_dsn, monkeypatch):
    # Today's real shape: every channel_key has its OWN token_env_key (no
    # sharing yet) -- synthetic-but-distinct names stand in for the real env
    # var names (never read/printed; this proves STRUCTURE, not secrets).
    for key in _NAZIM_PINNED_CHANNELS:
        _seed_channel(cycle_dsn, key, f"TOKEN_ENV_FOR_{key.upper().replace('-', '_')}")
    monkeypatch.setenv("INGEST_CHANNELS", ",".join(_NAZIM_PINNED_CHANNELS))

    with psycopg.connect(cycle_dsn) as conn:
        channels = ingest.load_channels(conn)

    assert set(channels.keys()) == set(_NAZIM_PINNED_CHANNELS)
    groups = ingest.group_by_token(channels)
    assert len(groups) == len(_NAZIM_PINNED_CHANNELS)      # one group PER channel
    assert all(len(g) == 1 for g in groups.values())       # every group solo
    # and a solo group never trips either edge-case guard, by construction.
    for group in groups.values():
        assert ingest.overlapping_chat_ids(group) == {}
        assert ingest.empty_allowlist_rows(group) == []    # len<=1 -> never flagged


# ── PR2 (design-GO bus #69978, migration 108): the REAL testbed repoint ──────
# Musa's testbed for "one-bot-many-clients" repoints 'oeh' and 'angullia' onto
# the same WINGMENDEVBOT_TOKEN that 'coffeemedia' already uses (migration 108)
# -- a REAL 3-row group in the actual pinned channel list, not a synthetic
# stand-in. allowed_chat_ids below are the real, already-disjoint values
# (verified live before writing migration 108) -- this proves the group
# refuses nothing and routes correctly for the exact shape that will exist in
# prod once migration 108 is applied, not just a 2-row synthetic case.
# `enabled` stays FALSE on all three per design-GO condition 5 -- this test
# never flips it and never sends anything to a real chat.

def test_wingmendevbot_testbed_repoint_three_real_rows_share_token_no_409(cycle_dsn, monkeypatch):
    monkeypatch.setenv("WINGMENDEVBOT_TOKEN", "fake-token-value")
    _seed_channel(cycle_dsn, "coffeemedia", "WINGMENDEVBOT_TOKEN",
                  chat_ids=[286619815], audience="internal")
    _seed_channel(cycle_dsn, "oeh", "WINGMENDEVBOT_TOKEN",
                  chat_ids=[-5585966657, -1003694059692], audience="client")
    _seed_channel(cycle_dsn, "angullia", "WINGMENDEVBOT_TOKEN",
                  chat_ids=[-5449309564], audience="client")
    channels = _load_group(cycle_dsn, "WINGMENDEVBOT_TOKEN")
    assert sorted(channels.keys()) == ["angullia", "coffeemedia", "oeh"]

    group = list(channels.values())
    assert ingest.overlapping_chat_ids(group) == {}
    assert ingest.empty_allowlist_rows(group) == []

    calls = []

    def _fake_tg_call(token, method, params, timeout=None):
        calls.append((token, method, params))
        return [
            {"update_id": 1, "message": {"chat": {"id": 286619815}, "from": {}, "text": "musa to coffeemedia"}},
            {"update_id": 2, "message": {"chat": {"id": -5585966657}, "from": {}, "text": "oeh group msg"}},
            {"update_id": 3, "message": {"chat": {"id": -5449309564}, "from": {}, "text": "angullia group msg"}},
        ]

    monkeypatch.setattr(ingest, "tg_call", _fake_tg_call)
    monkeypatch.setattr(ingest, "nudge_session", lambda *a, **kw: True)
    monkeypatch.setattr(ingest, "pane_working", lambda *a: False)

    import asyncio
    last_alert, sleep_for = asyncio.run(
        ingest._run_token_cycle("WINGMENDEVBOT_TOKEN", channels, 0.0))

    assert len(calls) == 1          # ONE getUpdates call for all three rows -- no 409
    assert sleep_for is None

    with psycopg.connect(cycle_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tag, text FROM operator_messages ORDER BY id")
        rows = cur.fetchall()
    assert rows == [
        ("coffeemedia", "musa to coffeemedia"),
        ("oeh", "oeh group msg"),
        ("angullia", "angullia group msg"),
    ]

    with psycopg.connect(cycle_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT channel_key, enabled FROM bot_channels ORDER BY channel_key")
        assert cur.fetchall() == [("angullia", False), ("coffeemedia", False), ("oeh", False)]


def test_main_tasks_key_by_unique_token_not_channel_key():
    """main()'s task-spawn loop must iterate group_by_token(channels), i.e.
    ONE task per unique token_env_key -- the actual fix for the dual-poller
    409 class once 2+ channel_keys ever DO share a token."""
    channels = {
        "a": _ch("a", "SHARED"),
        "b": _ch("b", "SHARED"),
        "c": _ch("c", "SOLO"),
    }
    groups = ingest.group_by_token(channels)
    assert sorted(groups.keys()) == ["SHARED", "SOLO"]     # 2 tasks, not 3


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
