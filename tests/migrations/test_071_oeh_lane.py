"""Ephemeral wet-prove for migration 071 — oeh lane onboarding (op#22521,
bus 2cf1d727-32de-46f3-abf4-b8c8c3eeb4b1 #43427).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo, and never applies 071 there either —
this is the dry-run this migration is required to pass before it may ever be
applied for real (CLAUDE.md: substrate-DB migrations come to orch-console's gate
BEFORE apply, direct psycopg, never db push).

Scope is deliberately smaller than test_070_angullia_project_governance.py's:
071 touches ONLY agents + fleet_lanes (#43427: no project_governance/bot_channels
row yet), so the fixture schema here only creates those two tables plus the
pre-existing cosem rows used to prove no-collision.

Exercises the SHIPPED artifact for 071 itself: its DDL is read FROM
migrations/071_*.sql (not re-typed), so this test breaks if that file drifts from
what it proves.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "071_oeh_lane.sql")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))
import auto_agent_id  # noqa: E402
import lane_token_resolver  # noqa: E402


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    """Split on ';' at top level only, respecting `$$` dollar-quoting AND single-
    quoted string literals (same splitter as test_070 — 071's notes text also has
    literal `'` escapes a naive splitter would break on)."""
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
    # project_governance_families exists on the live substrate (063/070) but 071
    # deliberately does NOT write to it -- still needed here so project_for_agent()
    # (used by load_family_map) resolves the pre-existing cosem rows without error.
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


def _apply_071(cur):
    for stmt in _split_statements(_executable_sql(MIG)):
        cur.execute(stmt)


@pytest.fixture
def oeh_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_fixture_schema(cur)
        _apply_071(cur)
    return fresh_db


def test_071_combined_lane_repo_scope_and_desired_state_up(oeh_db):
    with psycopg.connect(oeh_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT repo_scope FROM agents WHERE id = 'cc-oeh'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == ["oeh"]

        cur.execute("SELECT desired_state, base_agent_id, worktree_path FROM fleet_lanes WHERE lane = 'oeh'")
        row = cur.fetchone()
        assert row is not None
        # #43427: boot immediately, hand cc-oeh its first task right away.
        assert row[0] == "up"
        assert row[1] == "cc-oeh"
        assert row[2] == "/Users/sheikhmusa/wingmen/projects/oeh"


def test_071_no_project_governance_or_bot_channel_rows(oeh_db):
    """#43427 explicitly says NOT to seed project_governance/bot_channels yet —
    confirm 071 didn't sneak either in (the fixture doesn't even define
    bot_channels, so this also proves 071 never references that table)."""
    with psycopg.connect(oeh_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'bot_channels'"
        )
        assert cur.fetchone()[0] == 0


def test_071_does_not_disturb_existing_cosem_family(oeh_db):
    with psycopg.connect(oeh_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT project_for_agent('cc-cosem-exams')")
        assert cur.fetchone()[0] == "cosem"


def test_071_load_family_map_no_collision(oeh_db):
    family_map = auto_agent_id.load_family_map(oeh_db)
    # 'oeh' has no project_governance_families row (071 doesn't write one), so
    # it won't appear in a family map keyed off that table -- what matters here
    # is that adding cc-oeh doesn't disturb the pre-existing cosem mappings.
    assert family_map["cosem-exams-lane"] == "cc-cosem-exams"
    assert family_map["cosem-adcda-hotfix"] == "cc-cosem-adcda"


def test_071_is_idempotent(oeh_db):
    with psycopg.connect(oeh_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_071(cur)  # re-apply must not raise / must not duplicate
        cur.execute("SELECT count(*) FROM agents WHERE id = 'cc-oeh'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM fleet_lanes WHERE lane = 'oeh'")
        assert cur.fetchone()[0] == 1


def test_071_token_resolver_family_of_oeh_no_code_change_needed():
    """'oeh' is a single-word family: family_of()'s existing strip-cc-then-
    split-on-first-hyphen path already resolves it correctly with NO
    _COMPOUND_FAMILIES entry."""
    assert lane_token_resolver.family_of("cc-oeh") == "oeh"
    assert lane_token_resolver.family_of("oeh") == "oeh"
    assert lane_token_resolver.family_of("oeh-worker-1") == "oeh"
    # unaffected: cosem-tdu's compound-family carve-out still works
    assert lane_token_resolver.family_of("cc-cosem-tdu-coord") == "cosem-tdu"


def test_071_group_pointer_resolves_to_syed_pool(tmp_path):
    """The live .group_default_token.oeh pointer file (created alongside this
    PR, on the LIVE orchestrator dir — not this worktree) must point at the
    same Syed-pool key file angullia/cosem-tdu's pointer uses, so cc-oeh boots
    on Syed's token, not Musa's default."""
    syed_key = tmp_path / "syed-oauth-token"
    syed_key.write_text("fake-syed-token-for-test\n")
    orch_dir = tmp_path
    (orch_dir / ".group_default_token.oeh").write_text(str(syed_key) + "\n")
    resolved = lane_token_resolver.resolve_lane_token_path("cc-oeh", orch_dir=str(orch_dir))
    assert resolved == str(syed_key)
