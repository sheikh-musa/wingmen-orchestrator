"""Sender attribution (ingest.extract_sender + chat_members roster + chat_roster).

Pure-logic tests (extraction, authority check) run everywhere. The roster
upsert idempotency test runs the REAL ingest.ROSTER_UPSERT_SQL against an
EPHEMERAL TEMP TABLE inside a rolled-back transaction, so it never touches the
live chat_members / applies the migration. Skips cleanly when no DSN.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from nervous_system import chat_roster, ingest  # noqa: E402

psycopg = pytest.importorskip("psycopg")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
db_only = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set — DB test skipped")


# ── extract_sender: NULL-safe attribution ──────────────────────────────────────

def test_extract_sender_missing_from():
    """Channel posts / service messages carry no `from` — all-None, never throws."""
    assert ingest.extract_sender({}) == {"user_id": None, "username": None, "name": None}
    assert ingest.extract_sender({"from": None}) == {
        "user_id": None, "username": None, "name": None}


def test_extract_sender_normal_dm():
    s = ingest.extract_sender({"from": {
        "id": 286619815, "username": "musa", "first_name": "Sheikh", "last_name": "Musa"}})
    assert s == {"user_id": "286619815", "username": "musa", "name": "Sheikh Musa"}
    assert isinstance(s["user_id"], str)   # id coerced to text (matches column type)


def test_extract_sender_partial_first_name_only():
    """No last_name, no username — name is just the first name, username None."""
    s = ingest.extract_sender({"from": {"id": 42, "first_name": "Shen"}})
    assert s == {"user_id": "42", "username": None, "name": "Shen"}


def test_extract_sender_id_only():
    """id present but no name parts → name None (empty join is not stored as '')."""
    s = ingest.extract_sender({"from": {"id": 7}})
    assert s == {"user_id": "7", "username": None, "name": None}


# ── is_authorized_principal: ONLY the operator commands the orchestrator ─────────

def test_is_authorized_principal(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", "286619815")
    assert chat_roster.is_authorized_principal("286619815") is True
    assert chat_roster.is_authorized_principal(286619815) is True   # int coerced
    assert chat_roster.is_authorized_principal("999") is False      # a group sender
    assert chat_roster.is_authorized_principal(None) is False


def test_is_authorized_principal_no_env(monkeypatch):
    """Fail-closed: no MUSA_TELEGRAM_ID configured → nobody is a principal."""
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    assert chat_roster.is_authorized_principal("286619815") is False


# ── roster upsert idempotency (real SQL, ephemeral temp table) ──────────────────

@db_only
def test_roster_upsert_idempotency():
    with psycopg.connect(_DSN, autocommit=False) as conn, conn.cursor() as cur:
        # TEMP TABLE shadows the (possibly-absent) real chat_members in search_path,
        # so ingest.ROSTER_UPSERT_SQL exercises the exact production statement.
        cur.execute("""
            CREATE TEMP TABLE chat_members (
              chat_id       text NOT NULL,
              user_id       text NOT NULL,
              username      text,
              display_name  text,
              known_label   text,
              first_seen_at timestamptz NOT NULL DEFAULT now(),
              last_seen_at  timestamptz NOT NULL DEFAULT now(),
              msg_count     integer NOT NULL DEFAULT 0,
              PRIMARY KEY (chat_id, user_id)
            ) ON COMMIT DROP
        """)
        # First sight: inserts with msg_count 0, then a human labels the row.
        cur.execute(ingest.ROSTER_UPSERT_SQL, ("100", "5", "shen", "Shen"))
        cur.execute("UPDATE chat_members SET known_label='Shen' WHERE user_id='5'")
        cur.execute("SELECT msg_count, known_label, username, display_name "
                    "FROM chat_members WHERE chat_id='100' AND user_id='5'")
        assert cur.fetchone() == (0, "Shen", "shen", "Shen")

        # Re-sight (username/display refreshed): count increments, label preserved.
        cur.execute(ingest.ROSTER_UPSERT_SQL, ("100", "5", "shen2", "Shen Two"))
        cur.execute("SELECT msg_count, known_label, username, display_name "
                    "FROM chat_members WHERE chat_id='100' AND user_id='5'")
        assert cur.fetchone() == (1, "Shen", "shen2", "Shen Two")   # label NOT clobbered

        # Third sight bumps again.
        cur.execute(ingest.ROSTER_UPSERT_SQL, ("100", "5", "shen2", "Shen Two"))
        cur.execute("SELECT msg_count FROM chat_members WHERE chat_id='100' AND user_id='5'")
        assert cur.fetchone()[0] == 2

        # A different (chat_id, user_id) is an independent row.
        cur.execute(ingest.ROSTER_UPSERT_SQL, ("100", "9", None, None))
        cur.execute("SELECT count(*) FROM chat_members WHERE chat_id='100'")
        assert cur.fetchone()[0] == 2
        conn.rollback()   # nothing persisted; temp table dropped
