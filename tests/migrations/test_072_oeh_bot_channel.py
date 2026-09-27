"""Ephemeral wet-prove for migration 072 — OEH channel wiring (op#22521,
bus #43713/#43719/#43724/#43775, orch-console P1 review_request + three same-day
follow-up decisions).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo, and never applies 072 there either —
this is the dry-run this migration is required to pass before it may ever be
applied for real (CLAUDE.md: substrate-DB migrations come to orch-console's gate
BEFORE apply, direct psycopg, never db push).

072 now mirrors 070_angullia_project_governance.sql's full shape (bot_channels +
project_governance + project_governance_families) — it originally shipped
bot_channels only, but bus #43719 (Sya getting the operator role), #43724
(project_governance_families required for cc-oeh's work to resolve to project
'oeh'), and #43775 (the OEH Telegram group went live at chat id -5585966657,
so the channel ships enabled with that chat id wired) widened it before merge.
Fixture schema mirrors test_070's approach, seeded with the pre-existing
'angullia' rows this migration's priority-10 'oeh' rows must NOT disturb.

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

    cur.execute("""
        CREATE TABLE public.project_governance (
          project                  TEXT        PRIMARY KEY,
          cai_enabled              BOOLEAN     NOT NULL,
          operators                JSONB       NOT NULL DEFAULT '[]'::jsonb,
          channels                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
          money_clearance_enabled  BOOLEAN     NOT NULL DEFAULT false,
          residency_ack            JSONB,
          updated_by               TEXT,
          reason                   TEXT,
          updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # pre-existing angullia project_governance row this migration must not disturb
    cur.execute("""
        INSERT INTO public.project_governance
          (project, cai_enabled, operators, channels, money_clearance_enabled, residency_ack, updated_by, reason)
        VALUES
          ('angullia', false, '[]'::jsonb, '[]'::jsonb, false, NULL, 'cc-substrate', 'pre-existing')
    """)

    cur.execute("""
        CREATE TABLE public.project_governance_families (
          id          SERIAL      PRIMARY KEY,
          match_type  TEXT        NOT NULL CHECK (match_type IN ('agent_prefix', 'repo_pattern')),
          pattern     TEXT        NOT NULL,
          project     TEXT        NOT NULL,
          priority    INTEGER     NOT NULL DEFAULT 100,
          UNIQUE (match_type, pattern)
        )
    """)
    # pre-existing angullia + cosem families this migration's priority-10 'oeh'
    # rows must not disturb (mirrors test_070's non-disturbance proof)
    cur.execute("""
        INSERT INTO public.project_governance_families (match_type, pattern, project, priority) VALUES
          ('agent_prefix', '^cc-angullia', 'angullia', 10),
          ('repo_pattern', '%angullia%',   'angullia', 10),
          ('agent_prefix', '^cc-cosem-tdu', 'cosem-tdu', 5),
          ('repo_pattern', '%cosem-tdu%',   'cosem-tdu', 5)
    """)
    cur.execute("""
        CREATE FUNCTION public.project_for_agent(agent TEXT)
        RETURNS TEXT LANGUAGE sql STABLE SET search_path = public AS $fn$
          SELECT f.project FROM public.project_governance_families f
           WHERE f.match_type = 'agent_prefix' AND agent ~ f.pattern
           ORDER BY f.priority, f.id LIMIT 1;
        $fn$
    """)
    cur.execute("""
        CREATE FUNCTION public.project_for_repo(repo TEXT)
        RETURNS TEXT LANGUAGE sql STABLE SET search_path = public AS $fn$
          SELECT f.project FROM public.project_governance_families f
           WHERE f.match_type = 'repo_pattern' AND repo ILIKE f.pattern
           ORDER BY f.priority, f.id LIMIT 1;
        $fn$
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


def test_072_bot_channel_live_group_enabled(oeh_channel_db):
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
        # bus #43775: the OEH group is live at this chat id and the channel ships enabled
        assert allowed_chat_ids == [-5585966657]
        assert group_routing["agent_phase"] == "supervised"
        assert group_routing["agent_reviewer"] == "cc-oeh"
        assert channel_tag == "oeh"
        assert log_target == "substrate"
        assert enabled is True


def test_072_does_not_disturb_existing_angullia_row(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT enabled, inject_target FROM bot_channels WHERE channel_key = 'angullia'")
        row = cur.fetchone()
        assert row == (False, "angullia")


def test_072_project_governance_row_seeded_pending(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cai_enabled, operators, channels, money_clearance_enabled, "
            "residency_ack, reason, updated_by FROM project_governance WHERE project = 'oeh'"
        )
        row = cur.fetchone()
        assert row is not None
        cai_enabled, operators, channels, money_clearance_enabled, residency_ack, reason, updated_by = row
        assert cai_enabled is False
        assert operators == []
        # bus #43775: channels wired to the live OEH group chat id
        assert channels == ["-5585966657"]
        assert money_clearance_enabled is False
        # bus #43719 leaves residency_ack NULL for now (website content only, no
        # personal data today) -- same deliberate-NULL pattern as angullia.
        assert residency_ack is None
        assert reason  # non-empty
        assert "Sya" in reason
        assert updated_by == "cc-substrate"


def test_072_does_not_disturb_existing_angullia_governance_row(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT reason FROM project_governance WHERE project = 'angullia'")
        row = cur.fetchone()
        assert row == ("pre-existing",)


def test_072_family_resolves_oeh_without_disturbing_angullia_or_cosem_tdu(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT project_for_agent('cc-oeh')")
        assert cur.fetchone()[0] == "oeh"
        cur.execute("SELECT project_for_repo('sheikh-musa/oeh')")
        assert cur.fetchone()[0] == "oeh"

        # unaffected: pre-existing angullia / cosem-tdu families still resolve correctly
        cur.execute("SELECT project_for_agent('cc-angullia')")
        assert cur.fetchone()[0] == "angullia"
        cur.execute("SELECT project_for_agent('cc-cosem-tdu-coord')")
        assert cur.fetchone()[0] == "cosem-tdu"


def test_072_family_rows_have_priority_10(oeh_channel_db):
    with psycopg.connect(oeh_channel_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT match_type, pattern, priority FROM project_governance_families "
            "WHERE project = 'oeh' ORDER BY match_type"
        )
        rows = cur.fetchall()
        assert rows == [
            ("agent_prefix", "^cc-oeh", 10),
            ("repo_pattern", "%/oeh%", 10),
        ]


def test_072_is_idempotent(oeh_channel_db):
    with psycopg.connect(oeh_channel_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_072(cur)  # re-apply must not raise / must not duplicate
        cur.execute("SELECT count(*) FROM bot_channels WHERE channel_key = 'oeh'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM project_governance WHERE project = 'oeh'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM project_governance_families WHERE project = 'oeh'")
        assert cur.fetchone()[0] == 2
