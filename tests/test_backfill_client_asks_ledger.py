"""scripts/backfill_client_asks_ledger.py — migration 085 backfill (Musa
op#23944, bus #47105 -> #47114 item 4). Round-trip against the ephemeral
operator_ledger_db fixture (tests/conftest.py) — never production."""
import importlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

bf = importlib.import_module("scripts.backfill_client_asks_ledger")


def _insert_inbound(conn, tag, text, created_at=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered, created_at) "
            "VALUES ('inbound','telegram',%s,%s,true, COALESCE(%s, now())) RETURNING id",
            (tag, text, created_at),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


@pytest.fixture
def channels_db(operator_ledger_db):
    """bot_channels.channel_key doubles as channel_tag for this test suite
    (fetch_candidates joins bc.channel_tag = om.tag) -- add the real column
    the production schema carries (migration 085 doesn't touch channel_tag,
    it pre-dates 085), so tests must add it to the ephemeral fixture's stub
    table rather than assume conftest.py already has it."""
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("ALTER TABLE bot_channels ADD COLUMN IF NOT EXISTS channel_tag text")
    return operator_ledger_db


def _insert_client_channel(conn, channel_key="gazzabyte-irsyad", tag="gazzabyte-irsyad",
                            owner_lane="cc-irsyad-coord"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_channels (channel_key, channel_tag, audience, owner_lane) "
            "VALUES (%s,%s,'client',%s)",
            (channel_key, tag, owner_lane),
        )
    conn.commit()


def test_fetch_candidates_finds_untracked_client_inbound(channels_db):
    with psycopg.connect(channels_db) as c:
        _insert_client_channel(c)
        mid = _insert_inbound(c, "gazzabyte-irsyad", "[__test__] please add feature X")
    with psycopg.connect(channels_db) as c:
        rows = bf.fetch_candidates(c, datetime.now(timezone.utc) - timedelta(days=7))
    assert [r["id"] for r in rows] == [mid]
    assert rows[0]["owner_lane"] == "cc-irsyad-coord"


def test_fetch_candidates_excludes_already_tracked_rows(channels_db):
    with psycopg.connect(channels_db) as c, c.cursor() as cur:
        _insert_client_channel(c)
        mid = _insert_inbound(c, "gazzabyte-irsyad", "[__test__] already tracked")
        cur.execute(
            "INSERT INTO operator_asks (ask, source_msg_id, ask_surface, chase_by) "
            "VALUES ('x', %s, 'client-channel', now() + interval '24 hours')", (mid,),
        )
        c.commit()
    with psycopg.connect(channels_db) as c:
        rows = bf.fetch_candidates(c, datetime.now(timezone.utc) - timedelta(days=7))
    assert rows == []


def test_fetch_candidates_excludes_operator_audience_channels(channels_db):
    with psycopg.connect(channels_db) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_channels (channel_key, channel_tag, audience) "
                "VALUES ('orch-channel','orch-channel','operator')"
            )
        c.commit()
        _insert_inbound(c, "orch-channel", "[__test__] not a client channel")
    with psycopg.connect(channels_db) as c:
        rows = bf.fetch_candidates(c, datetime.now(timezone.utc) - timedelta(days=7))
    assert rows == []


def test_fetch_candidates_excludes_outbound_direction(channels_db):
    with psycopg.connect(channels_db) as c, c.cursor() as cur:
        _insert_client_channel(c)
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered) "
            "VALUES ('outbound','telegram','gazzabyte-irsyad','[__test__] our own reply',true)"
        )
        c.commit()
    with psycopg.connect(channels_db) as c:
        rows = bf.fetch_candidates(c, datetime.now(timezone.utc) - timedelta(days=7))
    assert rows == []


def test_fetch_candidates_respects_lookback_window(channels_db):
    with psycopg.connect(channels_db) as c:
        _insert_client_channel(c)
        old = datetime.now(timezone.utc) - timedelta(days=30)
        _insert_inbound(c, "gazzabyte-irsyad", "[__test__] too old", created_at=old)
    with psycopg.connect(channels_db) as c:
        rows = bf.fetch_candidates(c, datetime.now(timezone.utc) - timedelta(days=7))
    assert rows == []


def test_main_dry_run_writes_nothing(channels_db, capsys):
    with psycopg.connect(channels_db) as c:
        _insert_client_channel(c)
        _insert_inbound(c, "gazzabyte-irsyad", "[__test__] dry run candidate")
    rc = bf.main(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 candidate row" in out
    assert "gazzabyte-irsyad -> cc-irsyad-coord: 1" in out
    with psycopg.connect(channels_db) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM operator_asks")
        (n,) = cur.fetchone()
    assert n == 0


def test_main_real_run_inserts_backdated_rows_and_reports_open_set(channels_db, capsys):
    with psycopg.connect(channels_db) as c:
        _insert_client_channel(c)
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        mid = _insert_inbound(c, "gazzabyte-irsyad", "[__test__] please add feature X",
                               created_at=three_days_ago)
    rc = bf.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inserted 1 row" in out
    with psycopg.connect(channels_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT source_msg_id, created_at, chase_by, closed_at FROM operator_asks"
        )
        source_msg_id, created_at, chase_by, closed_at = cur.fetchone()
    assert source_msg_id == mid
    assert abs((created_at - three_days_ago).total_seconds()) < 5
    assert chase_by < datetime.now(timezone.utc), "backdated row must already be overdue"
    assert closed_at is None
    assert "OVERDUE" in out
    assert "please add feature X" not in out, "no-PII: raw ask text must never print in the report"


def test_main_second_run_is_idempotent(channels_db, capsys):
    with psycopg.connect(channels_db) as c:
        _insert_client_channel(c)
        _insert_inbound(c, "gazzabyte-irsyad", "[__test__] run twice")
    bf.main([])
    capsys.readouterr()
    rc = bf.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inserted 0 row" in out
    with psycopg.connect(channels_db) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM operator_asks")
        (n,) = cur.fetchone()
    assert n == 1


# ── render_report: pure formatting, no-PII ───────────────────────────────────
def _row(id=1, delegated_to="cc-irsyad-coord", triage_state="captured",
         triage_summary=None, created_at=None, chase_by=None):
    return {
        "id": id, "delegated_to": delegated_to, "triage_state": triage_state,
        "triage_summary": triage_summary, "created_at": created_at, "chase_by": chase_by,
    }


def test_render_report_empty_set():
    assert "empty" in bf.render_report([])


def test_render_report_flags_overdue():
    now = datetime.now(timezone.utc)
    r = _row(created_at=now - timedelta(days=3), chase_by=now - timedelta(hours=1))
    out = bf.render_report([r], now=now)
    assert "OVERDUE" in out


def test_render_report_not_overdue_when_chase_by_in_future():
    now = datetime.now(timezone.utc)
    r = _row(created_at=now - timedelta(hours=1), chase_by=now + timedelta(hours=23))
    out = bf.render_report([r], now=now)
    assert "OVERDUE" not in out


def test_render_report_shows_untriaged_when_no_summary():
    now = datetime.now(timezone.utc)
    r = _row(created_at=now, chase_by=now + timedelta(hours=24), triage_summary=None)
    out = bf.render_report([r], now=now)
    assert "untriaged" in out


def test_render_report_shows_summary_when_triaged():
    now = datetime.now(timezone.utc)
    r = _row(created_at=now, chase_by=now + timedelta(hours=24),
             triage_summary="a human-reviewed one-liner")
    out = bf.render_report([r], now=now)
    assert "a human-reviewed one-liner" in out
