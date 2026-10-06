"""Ephemeral wet-prove for migration 093 — data_provenance.org_id canonicalized
to the full UUID (bus #53416, filed by cc-cosem-platform, routed via
orch-console #53414).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo.

Fixture schema applies migration 089 verbatim (the real data_provenance DDL +
seed rows), then 093 on top, so this test exercises the SHIPPED artifacts for
both — it breaks if either file drifts from what it proves. Without 093, the
two assertions in test_093_full_uuid_now_resolves_registered_rows would fail
(classify_data_provenance would return no row for the full UUID, reproducing
the exact false "unregistered" report bus #53416 describes), which is the
regression this test is pinned against.
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

MIG_089 = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "migrations" / "089_data_provenance.sql")
MIG_093 = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "migrations" / "093_data_provenance_org_id_full_uuid.sql")

ADCDA_UUID = "1478c9b2-ff44-4091-a67e-a1391303c4ce"
SYNTHETIC_UUID = "ba98da04-2a5e-46ba-97f8-387f17753bcc"


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    """Split on ';' at top level only, respecting `$$` dollar-quoting AND single-
    quoted string literals (same splitter as test_070/test_069 — these files'
    reason/evidence text also has literal `'` escapes a naive splitter would
    break on)."""
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


def _apply(cur, path: pathlib.Path) -> None:
    for stmt in _split_statements(_executable_sql(path)):
        cur.execute(stmt)


@pytest.fixture
def dataprov_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        # 089's DDL grants/policies target Supabase's standard roles, which the
        # ephemeral harness doesn't have by default (same pattern as
        # test_063_064_project_governance_backport.py).
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(f'DROP ROLE IF EXISTS {role}')
            cur.execute(f'CREATE ROLE {role}')
        _apply(cur, MIG_089)
        _apply(cur, MIG_093)
    return fresh_db


def test_093_full_uuid_now_resolves_registered_rows(dataprov_db):
    """The exact defect bus #53416 reports: a caller holding the full UUID
    (every real caller's actual shape, cosem-platform's `org_id UUID NOT NULL`
    column) must match the registered row, not silently fall through to
    UNCLASSIFIED."""
    with psycopg.connect(dataprov_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT classification, evidence FROM classify_data_provenance(%s, %s)",
            ("ywrpttpxwfcoodovxhsr", ADCDA_UUID),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "MIXED_PENDING_REAL"

        cur.execute(
            "SELECT classification, evidence FROM classify_data_provenance(%s, %s)",
            ("ywrpttpxwfcoodovxhsr", SYNTHETIC_UUID),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "SYNTHETIC"


def test_093_synthetic_org_name_updated(dataprov_db):
    with psycopg.connect(dataprov_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT org_name FROM data_provenance WHERE project_ref = %s AND org_id = %s",
            ("ywrpttpxwfcoodovxhsr", SYNTHETIC_UUID),
        )
        assert cur.fetchone()[0] == "Meridian Training Academy (Synthetic Demo)"


def test_093_old_short_prefix_no_longer_resolves(dataprov_db):
    """The short 8-char form stays fine in prose, but after 093 it is no
    longer a value classify_data_provenance() matches — the stored org_id has
    moved to the full UUID. A caller that still passes the short form gets
    UNCLASSIFIED (fail-safe), not a false match on the wrong row."""
    with psycopg.connect(dataprov_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM classify_data_provenance(%s, %s)",
            ("ywrpttpxwfcoodovxhsr", "1478c9b2"),
        )
        assert cur.fetchone() is None

        cur.execute(
            "SELECT * FROM classify_data_provenance(%s, %s)",
            ("ywrpttpxwfcoodovxhsr", "ba98da04"),
        )
        assert cur.fetchone() is None


def test_093_check_constraint_rejects_short_prefix_insert(dataprov_db):
    """Enforce in code, not by promise: a future seed/insert cannot silently
    reintroduce an 8-char-prefix row and recreate this exact trap."""
    with psycopg.connect(dataprov_db, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO data_provenance "
                "(project_ref, org_id, alias, classification, evidence, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("ywrpttpxwfcoodovxhsr", "deadbeef", "test org", "SYNTHETIC", "test", "test"),
            )


def test_093_check_constraint_allows_full_uuid_and_default(dataprov_db):
    with psycopg.connect(dataprov_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO data_provenance "
            "(project_ref, org_id, alias, classification, evidence, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("ywrpttpxwfcoodovxhsr", "00000000-0000-0000-0000-000000000000", "test org",
             "SYNTHETIC", "test", "test"),
        )
        cur.execute(
            "INSERT INTO data_provenance "
            "(project_ref, org_id, alias, classification, evidence, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("some-other-project-ref", "", "test store", "SYNTHETIC", "test", "test"),
        )


def test_093_is_idempotent(dataprov_db):
    with psycopg.connect(dataprov_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply(cur, MIG_093)  # re-apply must not raise / must not duplicate
        cur.execute(
            "SELECT count(*) FROM data_provenance WHERE project_ref = %s",
            ("ywrpttpxwfcoodovxhsr",),
        )
        assert cur.fetchone()[0] == 3  # store-level + the two per-org rows
        cur.execute("SELECT count(*) FROM pg_constraint WHERE conname = %s",
                    ("data_provenance_org_id_full_uuid_or_default",))
        assert cur.fetchone()[0] == 1
