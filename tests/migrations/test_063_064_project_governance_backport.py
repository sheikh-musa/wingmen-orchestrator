"""Wet-prove tests for the 063/064 backport onto fable/substrate-safe-fixes
(op#22417 follow-on; orch-console bus #43319 authorization, thread
cce66db2-0b74-47fa-8938-53e4c5d66cf9).

migrations/063_project_governance.sql and 064_project_governance_cai_gate.sql
were built and live-applied to the substrate DB (tscuymavysscrvoberrr) on
`main`, but `main` and `fable/substrate-safe-fixes` (this repo's actual
deployed trunk) had diverged far enough that fable never picked them up --
fable jumps straight from 062 to 065+ (069 already present, added by PR #156).
This backport copies both files byte-identical (verified in the PR that added
this test) so fable's migrations/ directory matches what is actually live.

NOT a full 001->069 replay: this repo's migration set has never been
self-contained from an empty database. Several early-numbered files (and 064
itself, via `ALTER TABLE public.agent_messages` and a trigger on
`public.strategic_decisions`) assume tables that predate the migrations/
numbering convention and are not created by ANY tracked migration or
schema.sql (agent_messages, strategic_decisions -- same situation as the
legacy clients/payments/chat_history tables migration 013 touches). A
"replay everything from scratch" test would either fail on those or have to
silently fabricate schema no other test here vouches for. Instead, this file
follows the repo's existing per-migration wet-prove convention (see
test_069_cosem_tdu_project_governance.py) with the two legacy tables stubbed
down to exactly the columns 064's triggers touch -- proving 063 then 064
apply cleanly in sequence, their assertions hold, and the cai-gate triggers
they install behave per spec.

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

SILO = "tscuymavysscrvoberrr"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
MIG_063 = MIGRATIONS_DIR / "063_project_governance.sql"
MIG_064 = MIGRATIONS_DIR / "064_project_governance_cai_gate.sql"


def _make_ledger_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE migration_ledger (
                 repo text NOT NULL,
                 migration_name text NOT NULL,
                 silo_ref text NOT NULL,
                 sha256 text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text,
                 note text,
                 PRIMARY KEY (repo, migration_name, silo_ref)
               )"""
        )


def _bootstrap_roles_and_legacy_stubs(dsn: str) -> None:
    """Roles + minimal legacy-table stubs 063/064 assume already exist (see
    module docstring). Roles are cluster-global and survive across tests in
    this session, so every create is idempotent (DROP IF EXISTS first)."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(f'DROP ROLE IF EXISTS {role}')
            cur.execute(f'CREATE ROLE {role}')
        # Legacy stand-ins: real columns are far wider, but these are the only
        # ones 063 (none) / 064 (ALTER + trigger) actually touch.
        cur.execute(
            """CREATE TABLE public.agent_messages (
                 id           bigserial PRIMARY KEY,
                 from_agent   text,
                 to_agent     text,
                 message_type text,
                 body         text
               )"""
        )
        cur.execute(
            """CREATE TABLE public.strategic_decisions (
                 id             bigserial PRIMARY KEY,
                 decided_by     text,
                 repos_affected text[]
               )"""
        )


@pytest.fixture
def governance_dsn(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    _make_ledger_table(dsn)
    _bootstrap_roles_and_legacy_stubs(dsn)
    return dsn


def _apply(dsn: str, path: Path) -> dict:
    return am.apply_migration(dsn, path, silo=SILO, applied_by="test-063-064-backport")


# --------------------------------------------------------------------------------
# Both migrations apply cleanly, in order, with their own assertions passing.
# --------------------------------------------------------------------------------

def test_063_then_064_apply_cleanly_in_sequence(governance_dsn):
    r63 = _apply(governance_dsn, MIG_063)
    assert r63["status"] == "applied"

    r64 = _apply(governance_dsn, MIG_064)
    assert r64["status"] == "applied"
    # every -- assert: line in both headers must have actually been checked
    assert len(r64["assertions"]) == 7
    assert len(r63["assertions"]) == 4


def test_reapplying_063_is_a_no_op_already_applied(governance_dsn):
    _apply(governance_dsn, MIG_063)
    r = _apply(governance_dsn, MIG_063)
    assert r["status"] == "already_applied"


def test_063_seeds_expected_projects(governance_dsn):
    _apply(governance_dsn, MIG_063)
    with psycopg.connect(governance_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT project, cai_enabled FROM public.project_governance ORDER BY project")
        rows = dict(cur.fetchall())
    assert rows == {"cosem": False, "irsyad": False, "substrate": True}


# --------------------------------------------------------------------------------
# agent_messages cai-gate (Part 2 of 064): only review-seeking message_types to
# cai are blocked, and only when the sending project is cai_enabled=false.
# --------------------------------------------------------------------------------

def test_agent_messages_gate_blocks_review_request_from_cai_off_project(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation, match="cai is OFF for project irsyad"):
            cur.execute(
                "INSERT INTO public.agent_messages (from_agent, to_agent, message_type) "
                "VALUES ('cc-irsyad-coord', 'cai', 'review_request')"
            )


def test_agent_messages_gate_allows_update_from_cai_off_project(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        # 'update' is informational, never gated even for a cai-off project.
        cur.execute(
            "INSERT INTO public.agent_messages (from_agent, to_agent, message_type) "
            "VALUES ('cc-irsyad-coord', 'cai', 'update')"
        )
        cur.execute("SELECT count(*) FROM public.agent_messages")
        assert cur.fetchone()[0] == 1


def test_agent_messages_gate_allows_review_request_from_cai_on_project(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        # 'substrate' is cai_enabled=true in the seed.
        cur.execute(
            "INSERT INTO public.agent_messages (from_agent, to_agent, message_type) "
            "VALUES ('cc-fleet-health', 'cai', 'review_request')"
        )
        cur.execute("SELECT count(*) FROM public.agent_messages")
        assert cur.fetchone()[0] == 1


def test_agent_messages_gate_allows_ungoverned_agent(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        # no project_governance_families row matches this prefix -> NULL project
        # -> ungoverned -> passes regardless of message_type (per migration 064's
        # header: fleet/substrate bodies are deliberately absent from the seed).
        cur.execute(
            "INSERT INTO public.agent_messages (from_agent, to_agent, message_type) "
            "VALUES ('cc-orchestrator', 'cai', 'review_request')"
        )
        cur.execute("SELECT count(*) FROM public.agent_messages")
        assert cur.fetchone()[0] == 1


# --------------------------------------------------------------------------------
# strategic_decisions cai-gate (Part 3 of 064): blocks decided_by='cai' only when
# EVERY repos_affected element resolves to a cai_enabled=false project.
# --------------------------------------------------------------------------------

def test_strategic_decisions_gate_blocks_when_every_repo_is_cai_off(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation, match="cai is OFF for every project"):
            cur.execute(
                "INSERT INTO public.strategic_decisions (decided_by, repos_affected) "
                "VALUES ('cai', ARRAY['cosem-adcda'])"
            )


def test_strategic_decisions_gate_passes_when_one_repo_is_ungoverned(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        # 'orchestrator' matches no repo_pattern family -> ungoverned -> the whole
        # row passes even though 'irsyad' alone would resolve to a cai-off project
        # (mirrors the migration header's own CAI-RESP-1422..1425 example).
        cur.execute(
            "INSERT INTO public.strategic_decisions (decided_by, repos_affected) "
            "VALUES ('cai', ARRAY['irsyad', 'orchestrator'])"
        )
        cur.execute("SELECT count(*) FROM public.strategic_decisions")
        assert cur.fetchone()[0] == 1


def test_strategic_decisions_gate_ignores_non_cai_decider(governance_dsn):
    _apply(governance_dsn, MIG_063)
    _apply(governance_dsn, MIG_064)
    with psycopg.connect(governance_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.strategic_decisions (decided_by, repos_affected) "
            "VALUES ('musa', ARRAY['cosem-adcda'])"
        )
        cur.execute("SELECT count(*) FROM public.strategic_decisions")
        assert cur.fetchone()[0] == 1
