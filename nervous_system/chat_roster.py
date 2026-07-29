"""chat_roster.py — read/label the self-learning sender roster (chat_members).

ingest.py upserts a chat_members row per (chat_id, user_id) as messages arrive
(see migrations/025_chat_members.sql), so an inbound message can be attributed
to WHO sent it. This module is the read/label surface over that table.

AUTHORITY: is_authorized_principal() is the command-authority check. ONLY the
operator (MUSA_TELEGRAM_ID) commands the orchestrator. Group senders — Shen, a
client in the Gazzabyte group, any partner — appear in the roster for
ATTRIBUTION but are NEVER principals; their messages are context, not orders.
(Sanctioned delegates such as cai/Nazim may be added here later; today it is
the operator alone.)

Connection pattern mirrors operator_log.py: DATABASE_URL or SUPABASE_DB_URL.
"""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def who_is(chat_id, user_id) -> dict | None:
    """Return {user_id, username, display_name, known_label} for a roster row,
    or None if we've never seen this (chat_id, user_id)."""
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, username, display_name, known_label "
            "FROM chat_members WHERE chat_id=%s AND user_id=%s",
            (str(chat_id), str(user_id)),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"user_id": row[0], "username": row[1],
            "display_name": row[2], "known_label": row[3]}


def is_authorized_principal(user_id) -> bool:
    """THE authority check: True only for the operator (MUSA_TELEGRAM_ID).

    Only the operator commands the orchestrator. Group senders (Shen, clients,
    partners) are never principals no matter how often they appear in the
    roster. Extend here for cai/Nazim delegates when that is sanctioned."""
    op = os.environ.get("MUSA_TELEGRAM_ID")
    return op is not None and str(user_id) == op


def set_label(chat_id, user_id, label) -> bool:
    """Set the human-curated known_label for a roster row. Returns True if a row
    was updated (the member must already exist — labels annotate seen senders)."""
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE chat_members SET known_label=%s WHERE chat_id=%s AND user_id=%s",
            (label, str(chat_id), str(user_id)),
        )
        n = cur.rowcount
        conn.commit()
    return n > 0
