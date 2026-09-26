"""Ephemeral wet-prove for migration 069 — cosem-tdu project_governance onboarding
(op#22426, bus cce66db2-0b74-47fa-8938-53e4c5d66cf9 #43308/#43310/#43311).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo, and never applies 069 there either —
this is the dry-run this migration is required to pass before it may ever be
applied for real (CLAUDE.md: substrate-DB migrations come to orch-console's gate
BEFORE apply, direct psycopg, never db push).

The DDL for project_governance / project_governance_families / project_for_agent()
/ project_for_repo() is NOT present as a migration FILE on this trunk (fable/
substrate-safe-fixes is missing 063/064, which only exist on `main` — see 069's
own header note) — it is hand-built here as a minimal fixture mirroring the real,
live shape (same approach as test_060_reject_substrate.py takes for
agent_messages), seeded with the two pre-existing families (cosem, at the real
priority=10) that 069's new priority=5 cosem-tdu rows must out-rank.

Exercises the SHIPPED artifact for 069 itself: its DDL is read FROM
migrations/069_*.sql (not re-typed), so this test breaks if that file drifts from
what it proves.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "069_cosem_tdu_project_governance.sql")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))
import auto_agent_id  # noqa: E402


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    """Split on ';' at top level only, respecting `$$` dollar-quoting AND single-
    quoted string literals (069's reason text has an em-dash `--` and a literal
    `;` inside a quoted string — a naive $$-only splitter breaks mid-statement
    on that `;`, doubled '' escapes handled by re-entering the string)."""
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
    # -- project_governance (real shape, migration 063 on `main`) --
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

    # -- project_governance_families + resolver functions (real shape, migration
    # 064 on `main`) --
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
    cur.execute("""
        INSERT INTO public.project_governance_families (match_type, pattern, project, priority) VALUES
          ('agent_prefix', '^cc-cosem', 'cosem', 10),
          ('repo_pattern', '%cosem%',   'cosem', 10)
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

    # -- bot_channels (real shape, migration 014 on this trunk) --
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

    # -- agents / fleet_lanes (real live column shapes) --
    cur.execute("""
        CREATE TABLE public.agents (
          id             TEXT PRIMARY KEY,
          display_name   TEXT NOT NULL,
          repo_scope     TEXT[] NOT NULL DEFAULT '{}',
          status         TEXT NOT NULL DEFAULT 'idle',
          current_task   TEXT,
          last_heartbeat TIMESTAMPTZ,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("""
        INSERT INTO public.agents (id, display_name, repo_scope) VALUES
          ('cc-cosem-tdu', 'cc-cosem-tdu — cosem-tdu repo (admin attendance, geofences, wages)', '{cosem-tdu}'),
          ('cc-cosem-exams', 'cc-cosem-exams', '{cosem-exams-lane}')
    """)
    cur.execute("""
        CREATE TABLE public.fleet_lanes (
          lane           TEXT PRIMARY KEY,
          worktree_path  TEXT NOT NULL,
          branch         TEXT NOT NULL,
          model          TEXT,
          launcher       TEXT,
          base_agent_id  TEXT,
          desired_state  TEXT NOT NULL DEFAULT 'down',
          notes          TEXT,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def _apply_069(cur):
    for stmt in _split_statements(_executable_sql(MIG)):
        cur.execute(stmt)


@pytest.fixture
def cosem_tdu_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_fixture_schema(cur)
        _apply_069(cur)
    return fresh_db


def test_069_project_governance_row_seeded_pending(cosem_tdu_db):
    with psycopg.connect(cosem_tdu_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cai_enabled, operators, channels, money_clearance_enabled, "
            "residency_ack, reason FROM project_governance WHERE project = 'cosem-tdu'"
        )
        row = cur.fetchone()
        assert row is not None
        cai_enabled, operators, channels, money_clearance_enabled, residency_ack, reason = row
        assert cai_enabled is False
        assert operators == []
        assert channels == []
        assert money_clearance_enabled is False
        # residency_ack MUST stay NULL — op#20706, Musa/Fazlie's call, never this migration's.
        assert residency_ack is None
        assert "op#22426" in reason


def test_069_family_precedence_cosem_tdu_wins_over_cosem(cosem_tdu_db):
    with psycopg.connect(cosem_tdu_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT project_for_agent('cc-cosem-tdu-coord')")
        assert cur.fetchone()[0] == "cosem-tdu"
        cur.execute("SELECT project_for_agent('cc-cosem-tdu')")
        assert cur.fetchone()[0] == "cosem-tdu"
        cur.execute("SELECT project_for_repo('cosem-tdu')")
        assert cur.fetchone()[0] == "cosem-tdu"
        cur.execute("SELECT project_for_repo('tdu-tools-prod')")
        assert cur.fetchone()[0] == "cosem-tdu"

        # unaffected: the broader 'cosem' family still resolves everyone else
        cur.execute("SELECT project_for_agent('cc-cosem-exams')")
        assert cur.fetchone()[0] == "cosem"
        cur.execute("SELECT project_for_repo('cosem-adcda')")
        assert cur.fetchone()[0] == "cosem"


def test_069_bot_channel_disabled_deny_by_default(cosem_tdu_db):
    with psycopg.connect(cosem_tdu_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT enabled, allowed_chat_ids, inject_target, group_routing "
            "FROM bot_channels WHERE channel_key = 'cosem-tdu'"
        )
        row = cur.fetchone()
        assert row is not None
        enabled, allowed_chat_ids, inject_target, group_routing = row
        assert enabled is False
        assert allowed_chat_ids == []
        assert inject_target == "cosem-tdu-coord"
        assert group_routing["agent_reviewer"] == "cc-cosem-tdu-coord"


def test_069_coord_lane_repo_scope_empty_and_down(cosem_tdu_db):
    with psycopg.connect(cosem_tdu_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT repo_scope FROM agents WHERE id = 'cc-cosem-tdu-coord'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == []

        cur.execute("SELECT desired_state, base_agent_id FROM fleet_lanes WHERE lane = 'cosem-tdu-coord'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "down"
        assert row[1] == "cc-cosem-tdu-coord"


def test_069_load_family_map_no_collision(cosem_tdu_db):
    """The new cc-cosem-tdu-coord agent row (repo_scope=[]) must never collide
    with the pre-existing cc-cosem-tdu builder (repo_scope=['cosem-tdu']) in
    scripts/lib/auto_agent_id.py's load_family_map() — that's the whole reason
    the coord's repo_scope is seeded empty."""
    family_map = auto_agent_id.load_family_map(cosem_tdu_db)
    assert family_map["cosem-tdu"] == "cc-cosem-tdu"
    assert "cc-cosem-tdu-coord" not in family_map.values()


def test_069_is_idempotent(cosem_tdu_db):
    with psycopg.connect(cosem_tdu_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_069(cur)  # re-apply must not raise / must not duplicate
        cur.execute("SELECT count(*) FROM project_governance WHERE project = 'cosem-tdu'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM project_governance_families WHERE project = 'cosem-tdu'")
        assert cur.fetchone()[0] == 3
