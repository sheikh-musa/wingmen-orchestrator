"""scripts/seed_build_programmes.py + scripts/update_programme_phases.py — the
build-status view's data path (Musa op#34918, orch-console 78503 plan / 78505 GO).
Round-trip against an ephemeral Postgres carrying the real operator_backlog +
held_commitments column shape — never production (tests/conftest.py guard)."""
import importlib
import json

import psycopg
import pytest

from tests.conftest import assert_dsn_is_not_production

seed = importlib.import_module("scripts.seed_build_programmes")
upd = importlib.import_module("scripts.update_programme_phases")


@pytest.fixture
def programmes_db(pg_dsn):
    """Ephemeral PG with operator_backlog (incl. the migration-122 phases jsonb)
    + held_commitments in the exact column shape programme_stall_guard uses."""
    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE operator_backlog (
                id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                ask           text NOT NULL,
                status        text NOT NULL,
                op_ref        text,
                note          text,
                sort_order    integer,
                updated_at    timestamptz NOT NULL DEFAULT now(),
                done_when     text,
                done_signal   jsonb,
                blocked_on    text,
                blocked_since timestamptz,
                phases        jsonb
            )
        """)
        cur.execute("""
            CREATE TABLE held_commitments (
                id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                owner_agent text NOT NULL,
                title       text NOT NULL,
                payload     text,
                due_at      timestamptz,
                status      text NOT NULL,
                source_ref  text,
                created_by  text,
                created_at  timestamptz NOT NULL DEFAULT now()
            )
        """)
    return pg_dsn


@pytest.fixture
def seeded_db(programmes_db):
    """programmes_db with the seed already applied — the helper's world."""
    assert seed.main(["--dsn", programmes_db]) == 0
    return programmes_db


@pytest.fixture
def bare_backlog_db(pg_dsn):
    """operator_backlog WITHOUT the phases column — the seed must refuse
    fail-closed (the migration has not been applied) rather than half-seed."""
    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE operator_backlog (
                id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                ask           text NOT NULL,
                status        text NOT NULL,
                updated_at    timestamptz NOT NULL DEFAULT now()
            )
        """)
    return pg_dsn


# ── seed ───────────────────────────────────────────────────────────────────

def test_seed_refuses_without_phases_column(bare_backlog_db):
    """Fail-closed: no phases column = migration 125 not applied = no seeding."""
    with pytest.raises(SystemExit, match=r"(?i)has no .phases. column"):
        seed.main(["--dsn", bare_backlog_db])


def test_seed_creates_three_programmes_with_owners_and_checkpoints(programmes_db):
    assert seed.main(["--dsn", programmes_db]) == 0
    with psycopg.connect(programmes_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ask, phases->>'owner' FROM operator_backlog "
            "WHERE op_ref = 'op#34918' ORDER BY id"
        )
        rows = cur.fetchall()
        # 78505: THREE rows — substrate 2.0 / memory 2.0 incl second brain /
        # TG 2.0. Rows 1+2 owned by cc-paperclip-spike-1 (joint plan 78481),
        # row 3 by cc-bariq-1 (Bariq as client 1).
        assert len(rows) == 3
        owners = [r[1] for r in rows]
        assert owners == ["cc-paperclip-spike-1", "cc-paperclip-spike-1", "cc-bariq-1"]
        assert "substrate 2.0" in rows[0][0].lower()
        assert "memory 2.0" in rows[1][0].lower()
        assert "tg 2.0" in rows[2][0].lower()
        # every programme starts with at least one phase + a live checkpoint
        # (programme_stall_guard pages an open programme with none)
        cur.execute(
            "SELECT count(*) FROM operator_backlog b "
            "WHERE b.op_ref = 'op#34918' "
            "  AND jsonb_array_length(b.phases->'phases') >= 1"
        )
        assert cur.fetchone()[0] == 3
        cur.execute(
            "SELECT count(DISTINCT (payload::jsonb->>'backlog_id')::bigint) "
            "FROM held_commitments WHERE status = 'pending'"
        )
        assert cur.fetchone()[0] == 3


def test_seed_is_idempotent(programmes_db):
    assert seed.main(["--dsn", programmes_db]) == 0
    assert seed.main(["--dsn", programmes_db]) == 0  # second run: no-op, exit 0
    with psycopg.connect(programmes_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operator_backlog WHERE op_ref = 'op#34918'"
        )
        assert cur.fetchone()[0] == 3  # still three, not six
        cur.execute("SELECT count(*) FROM held_commitments")
        assert cur.fetchone()[0] == 3  # one checkpoint each, not two


# ── update helper ──────────────────────────────────────────────────────────

def _one_programme_id(dsn) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM operator_backlog "
            "WHERE ask ILIKE '%tg 2.0%' LIMIT 1"
        )
        return cur.fetchone()[0]


def test_update_rejects_wrong_owner(seeded_db):
    pid = _one_programme_id(seeded_db)
    with pytest.raises(SystemExit, match="(?i)owner"):
        upd.main([
            "--dsn", seeded_db, "--programme", str(pid),
            "--by", "cc-random-lane", "--op", "op#34999",
            "--phases-json", json.dumps({
                "phases": [{"name": "onboarding", "status": "building", "date": "2026-11-02"}],
            }),
        ])


def test_update_rejects_bad_phase_status(seeded_db):
    pid = _one_programme_id(seeded_db)
    with pytest.raises(SystemExit, match="(?i)status"):
        upd.main([
            "--dsn", seeded_db, "--programme", str(pid),
            "--by", "cc-bariq-1", "--op", "op#34999",
            "--phases-json", json.dumps({
                "phases": [{"name": "onboarding", "status": "vibing", "date": "2026-11-02"}],
            }),
        ])


def test_update_writes_phases_with_agent_and_op_citation(seeded_db):
    pid = _one_programme_id(seeded_db)
    payload = {
        "phases": [
            {"name": "Bariq channel live", "status": "building", "date": "2026-11-02"},
            {"name": "Invite deep-link onboarding", "status": "next", "date": "2026-11-09"},
        ],
        "last_progress": {"at": "2026-10-28T09:00:00Z", "line": "channel provisioned, invite link drafted"},
    }
    rc = upd.main([
        "--dsn", seeded_db, "--programme", str(pid),
        "--by", "cc-bariq-1", "--op", "op#34918",
        "--phases-json", json.dumps(payload),
    ])
    assert rc == 0
    with psycopg.connect(seeded_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT phases FROM operator_backlog WHERE id = %s", (pid,))
        got = cur.fetchone()[0]
    # owner preserved (helper never lets an update change ownership)
    assert got["owner"] == "cc-bariq-1"
    assert got["phases"][0]["status"] == "building"
    # the audit trail the GO requires: updating agent + op citation ride inside
    assert got["updated_by"] == "cc-bariq-1"
    assert got["op"] == "op#34918"
    assert got["last_progress"]["line"].startswith("channel provisioned")


def test_update_rejects_unknown_programme(seeded_db):
    with pytest.raises(SystemExit, match="(?i)no operator_backlog row"):
        upd.main([
            "--dsn", seeded_db, "--programme", "99999",
            "--by", "cc-bariq-1", "--op", "op#34918",
            "--phases-json", json.dumps({
                "phases": [{"name": "anything", "status": "next"}],
            }),
        ])
