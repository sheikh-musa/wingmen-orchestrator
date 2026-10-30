"""unprocessed()'s coalesce_with_prev field (Musa op#36085/36098, 2026-10-30):
two of his messages sent seconds apart were treated as separate thoughts. This
is advisory, append-only (position 10), backward-compatible per the function's
own documented convention. DB tests run on the ephemeral PG fixture, never the
live substrate."""
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nervous_system import operator_log as ol  # noqa: E402


def _seed_at(dsn, rows):
    """rows: list of (tag, text, chat_id, from_user_id, created_at). Returns ids."""
    ids = []
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for tag, text, chat_id, fuid, created_at in rows:
            cur.execute(
                "INSERT INTO operator_messages "
                "(direction, channel, tag, text, chat_id, from_user_id, created_at) "
                "VALUES ('inbound','telegram',%s,%s,%s,%s,%s) RETURNING id",
                (tag, text, chat_id, fuid, created_at))
            ids.append(cur.fetchone()[0])
    return ids


@pytest.fixture
def ledger_db(operator_ledger_db):
    with psycopg.connect(operator_ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE operator_messages "
                    "ADD COLUMN IF NOT EXISTS handled_at timestamptz")
    return operator_ledger_db


@pytest.fixture
def lane_role(monkeypatch):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-oeh")
    ol._reset_read_cursor()


def test_two_messages_seconds_apart_same_sender_coalesce(ledger_db, lane_role):
    now = datetime.now(timezone.utc)
    _seed_at(ledger_db, [
        ("orch-channel", "first thought", "123", "musa", now),
        ("orch-channel", "same thought, continued", "123", "musa",
         now + timedelta(seconds=5)),
    ])
    rows = ol.unprocessed(tag="orch-channel")
    assert len(rows) == 2
    assert rows[0][-1] is False, "the first row in a batch has no predecessor to coalesce with"
    assert rows[1][-1] is True, "5s apart, same chat+sender -> coalesce"


def test_messages_far_apart_do_not_coalesce(ledger_db, lane_role):
    now = datetime.now(timezone.utc)
    _seed_at(ledger_db, [
        ("orch-channel", "first", "123", "musa", now),
        ("orch-channel", "much later", "123", "musa",
         now + timedelta(seconds=ol.COALESCE_WINDOW_SEC + 30)),
    ])
    rows = ol.unprocessed(tag="orch-channel")
    assert rows[1][-1] is False


def test_different_sender_does_not_coalesce_even_if_fast(ledger_db, lane_role):
    now = datetime.now(timezone.utc)
    _seed_at(ledger_db, [
        ("orch-channel", "from musa", "123", "musa", now),
        ("orch-channel", "from someone else", "123", "other-user",
         now + timedelta(seconds=2)),
    ])
    rows = ol.unprocessed(tag="orch-channel")
    assert rows[1][-1] is False


def test_different_chat_does_not_coalesce_even_if_fast(ledger_db, lane_role):
    now = datetime.now(timezone.utc)
    _seed_at(ledger_db, [
        ("orch-channel", "chat A", "111", "musa", now),
        ("orch-channel", "chat B", "222", "musa", now + timedelta(seconds=2)),
    ])
    rows = ol.unprocessed(tag="orch-channel")
    assert rows[1][-1] is False


def test_coalesce_field_is_appended_last_and_existing_fields_unmoved(ledger_db, lane_role):
    now = datetime.now(timezone.utc)
    ids = _seed_at(ledger_db, [("oeh", "hello", "9", "someone", now)])
    rows = ol.unprocessed(tag="oeh")
    row = rows[0]
    assert len(row) == 11
    assert row[0] == ids[0]
    assert row[2] == "hello"               # text position unchanged
    assert isinstance(row[-1], bool)        # new field, always present and boolean
