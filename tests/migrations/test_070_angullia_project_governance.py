"""Ephemeral wet-prove for migration 070 — angullia project_governance onboarding
(op#21154/op#22433, bus 26555a3f-3649-4b4f-adcf-aa041989fb8e #43320).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo, and never applies 070 there either —
this is the dry-run this migration is required to pass before it may ever be
applied for real (CLAUDE.md: substrate-DB migrations come to orch-console's gate
BEFORE apply, direct psycopg, never db push).

Fixture schema mirrors test_069_cosem_tdu_project_governance.py's approach (the
real DDL for these tables lives in migrations 063/064/014 on `main`, not on this
trunk yet — see 069's header note), seeded with the pre-existing 'cosem'/
'cosem-tdu' families (069 already proved those) plus the two rows this test's own
priority-10 angullia rows must NOT disturb.

Exercises the SHIPPED artifact for 070 itself: its DDL is read FROM
migrations/070_*.sql (not re-typed), so this test breaks if that file drifts from
what it proves.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "070_angullia_project_governance.sql")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))
import auto_agent_id  # noqa: E402
import lane_token_resolver  # noqa: E402


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    """Split on ';' at top level only, respecting `$$` dollar-quoting AND single-
    quoted string literals (same splitter as test_069 — 070's reason text also
    has literal `'` escapes and em-dashes that a naive splitter would break on)."""
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
          ('repo_pattern', '%cosem%',   'cosem', 10),
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
          ('cc-cosem-exams', 'cc-cosem-exams', '{cosem-exams-lane}'),
          ('cc-cosem-adcda', 'cc-cosem-adcda', '{cosem-adcda-hotfix}')
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


def _apply_070(cur):
    for stmt in _split_statements(_executable_sql(MIG)):
        cur.execute(stmt)


@pytest.fixture
def angullia_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_fixture_schema(cur)
        _apply_070(cur)
    return fresh_db


def test_070_project_governance_row_seeded_pending(angullia_db):
    with psycopg.connect(angullia_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cai_enabled, operators, channels, money_clearance_enabled, "
            "residency_ack, reason, updated_by FROM project_governance WHERE project = 'angullia'"
        )
        row = cur.fetchone()
        assert row is not None
        cai_enabled, operators, channels, money_clearance_enabled, residency_ack, reason, updated_by = row
        assert cai_enabled is False
        assert operators == []
        assert channels == []
        assert money_clearance_enabled is False
        # #43320 explicitly leaves residency_ack NULL for now (data shape not yet
        # known) — unlike cosem-tdu, there is deliberately no step-1b UPDATE here.
        assert residency_ack is None
        assert "Rhaihan pending id" in reason
        assert updated_by == "cc-substrate"


def test_070_family_resolves_angullia_without_disturbing_cosem(angullia_db):
    with psycopg.connect(angullia_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT project_for_agent('cc-angullia')")
        assert cur.fetchone()[0] == "angullia"
        cur.execute("SELECT project_for_repo('angullia')")
        assert cur.fetchone()[0] == "angullia"

        # unaffected: pre-existing cosem / cosem-tdu families still resolve correctly
        cur.execute("SELECT project_for_agent('cc-cosem-exams')")
        assert cur.fetchone()[0] == "cosem"
        cur.execute("SELECT project_for_agent('cc-cosem-tdu-coord')")
        assert cur.fetchone()[0] == "cosem-tdu"


def test_070_bot_channel_disabled_deny_by_default(angullia_db):
    with psycopg.connect(angullia_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT enabled, allowed_chat_ids, inject_target, group_routing "
            "FROM bot_channels WHERE channel_key = 'angullia'"
        )
        row = cur.fetchone()
        assert row is not None
        enabled, allowed_chat_ids, inject_target, group_routing = row
        assert enabled is False
        assert allowed_chat_ids == []
        assert inject_target == "angullia"
        assert group_routing["agent_reviewer"] == "cc-angullia"


def test_070_combined_lane_repo_scope_and_desired_state_up(angullia_db):
    with psycopg.connect(angullia_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT repo_scope FROM agents WHERE id = 'cc-angullia'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == ["angullia"]

        cur.execute("SELECT desired_state, base_agent_id, worktree_path FROM fleet_lanes WHERE lane = 'angullia'")
        row = cur.fetchone()
        assert row is not None
        # #43320: boot immediately, do not wait for the group.
        assert row[0] == "up"
        assert row[1] == "cc-angullia"
        assert row[2] == "/Users/sheikhmusa/wingmen/projects/angullia"


def test_070_load_family_map_no_collision(angullia_db):
    family_map = auto_agent_id.load_family_map(angullia_db)
    assert family_map["angullia"] == "cc-angullia"
    # doesn't disturb the pre-existing cosem/cosem-tdu mappings this fixture seeded
    assert family_map["cosem-exams-lane"] == "cc-cosem-exams"
    assert family_map["cosem-adcda-hotfix"] == "cc-cosem-adcda"


def test_070_is_idempotent(angullia_db):
    with psycopg.connect(angullia_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_070(cur)  # re-apply must not raise / must not duplicate
        cur.execute("SELECT count(*) FROM project_governance WHERE project = 'angullia'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM project_governance_families WHERE project = 'angullia'")
        assert cur.fetchone()[0] == 2


def test_070_token_resolver_family_of_angullia_no_code_change_needed():
    """'angullia' is a single-word family: family_of()'s existing strip-cc-then-
    split-on-first-hyphen path already resolves it correctly with NO
    _COMPOUND_FAMILIES entry (unlike cosem-tdu, which needed one because
    'cosem-tdu' would otherwise collapse to 'cosem' on the naive split)."""
    assert lane_token_resolver.family_of("cc-angullia") == "angullia"
    assert lane_token_resolver.family_of("angullia") == "angullia"
    assert lane_token_resolver.family_of("angullia-worker-1") == "angullia"
    # unaffected: cosem-tdu's compound-family carve-out still works
    assert lane_token_resolver.family_of("cc-cosem-tdu-coord") == "cosem-tdu"
    assert lane_token_resolver.family_of("cc-cosem-exams") == "cosem"


def test_070_group_pointer_resolves_to_syed_pool(tmp_path):
    """The live .group_default_token.angullia pointer file (created alongside
    this PR, on the LIVE orchestrator dir — not this worktree) must point at the
    same Syed-pool key file cosem-tdu's pointer uses, so cc-angullia boots on
    Syed's token, not Musa's default."""
    syed_key = tmp_path / "syed-oauth-token"
    syed_key.write_text("fake-syed-token-for-test\n")
    orch_dir = tmp_path
    (orch_dir / ".group_default_token.angullia").write_text(str(syed_key) + "\n")
    resolved = lane_token_resolver.resolve_lane_token_path("cc-angullia", orch_dir=str(orch_dir))
    assert resolved == str(syed_key)
