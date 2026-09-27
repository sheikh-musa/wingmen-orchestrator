"""Ephemeral wet-prove for migration 072 — OEH bot_channels wiring (op#22521,
bus #43713, orch-console P1 review_request).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo, and never applies 072 there either —
this is the dry-run this migration is required to pass before it may ever be
applied for real (CLAUDE.md: substrate-DB migrations come to orch-console's gate
BEFORE apply, direct psycopg, never db push).

Scope is deliberately smaller than test_070_angullia_project_governance.py's:
072 touches ONLY bot_channels (071 already onboarded the cc-oeh agent/lane and
deliberately deferred this row; #43713 asks only for the bot_channels row, not
project_governance) — the fixture schema here only creates bot_channels.

Exercises the SHIPPED artifact for 072 itself: its DDL is read FROM
migrations/072_*.sql (not re-typed), so this test breaks if that file drifts
from what it proves.
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "072_oeh_bot_channel.sql")


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    """Split on ';' at top level only, respecting `$$` dollar-quoting AND single-
    quoted string literals (same splitter as test_070/071)."""
    out, buf, in_dollar, in_str = [], [], False, False
    i = 0
    while i < len(sql):
        if not in_str and sql[i:i + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if not in_dollar and ch == "'":
            if in_str and sql[i:i + 2] == "''":
                buf.append("''")
                i += 2
                continue
            in_str = not in_str
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and not in_dollar and not in_str:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _make_fixture_schema(cur):
    cur.execute("""
        CREATE TABLE public.bot_channels (
          channel_key        TEXT PRIMARY KEY,
          token_env_key      TEXT NOT NULL,
          mode               TEXT NOT NULL CHECK (mode IN ('agent-session','ai-responder','log-and-route')),
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
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT agent_session_needs_target CHECK (mode <> 'agent-session' OR inject_target IS NOT NULL),
          CONSTRAINT ai_responder_needs_ref     CHECK (mode <> 'ai-responder'  OR responder_ref IS NOT NULL)
        )
    """)
    # pre-existing angullia row this migration must not disturb
    cur.execute("""
        INSERT INTO public.bot_channels
          (channel_key, token_env_key, mode, inject_target, inject_prefix,
           allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled)
        VALUES
          ('angullia', 'ANGULLIA_BOT_TOKEN', 'agent-session', 'angullia', 'Angullia: ',
           '{}', '{}', '{"agent_phase": "supervised", "agent_reviewer": "cc-angullia"}'::jsonb,
           'angullia', 'substrate', false)
    """)


def _apply_072(cur):
    for stmt in _split_statements(_executable_sql(MIG)):
        cur.execute(stmt)


@pytest.fixture
def oeh_channel_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_fixture_schema(cur)
        _apply_072(cur)
    return fresh_db


def test_072_bot_channel_disabled_deny_by_default(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT token_env_key, mode, inject_target, inject_prefix, "
            "allowed_chat_ids, group_routing, channel_tag, log_target, enabled "
            "FROM bot_channels WHERE channel_key = 'oeh'"
        )
        row = cur.fetchone()
        assert row is not None
        (token_env_key, mode, inject_target, inject_prefix,
         allowed_chat_ids, group_routing, channel_tag, log_target, enabled) = row
        assert token_env_key == "OEH_BOT_TOKEN"
        assert mode == "agent-session"
        assert inject_target == "oeh"
        assert "Sya" in inject_prefix
        assert allowed_chat_ids == []
        assert group_routing["agent_phase"] == "supervised"
        assert group_routing["agent_reviewer"] == "cc-oeh"
        assert channel_tag == "oeh"
        assert log_target == "substrate"
        assert enabled is False


def test_072_does_not_disturb_existing_angullia_row(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT enabled, inject_target FROM bot_channels WHERE channel_key = 'angullia'")
        row = cur.fetchone()
        assert row == (False, "angullia")


def test_072_is_idempotent(oeh_channel_db):
    with psycopg.connect(oeh_channel_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_072(cur)  # re-apply must not raise / must not duplicate
        cur.execute("SELECT count(*) FROM bot_channels WHERE channel_key = 'oeh'")
        assert cur.fetchone()[0] == 1
