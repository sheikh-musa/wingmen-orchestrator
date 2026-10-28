"""TDD proof for scripts/gen_governance_floors.py (op#35246 Phase 1a, PR#555
section 3: "ONE generated fenced block inserted into governance-floors.md
...holding the floors table + a live N of M floors passing line. The
surrounding prose stays hand-authored.")

Reuses gen_data_store_registry.py's exact partition-on-markers mechanism:
everything before START and after END is untouched byte-for-byte.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_governance_floors as ggf  # noqa: E402


def _make_substrate_ledger_row(cur):
    """Simulates the real substrate's migration_ledger row for 131 (console
    bus #80664) so a test exercises writing to the CANONICAL path, not the
    --out escape hatch."""
    cur.execute(
        """CREATE TABLE migration_ledger (
             id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
             repo text, migration_name text NOT NULL, silo_ref text NOT NULL,
             sha256 text, applied_at timestamptz NOT NULL DEFAULT now(),
             applied_by text, note text
           )"""
    )
    cur.execute(
        "INSERT INTO migration_ledger (migration_name, silo_ref) "
        "VALUES ('131_governance_floor.sql', %s)",
        (ggf.SUBSTRATE_SILO_REF,),
    )


@pytest.fixture
def floor_db(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE governance_floor (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 sentence text NOT NULL, op_ref text NOT NULL,
                 predicate_sql text NOT NULL, enforced_in_code boolean NOT NULL DEFAULT false,
                 decision_ref text, retired_at timestamptz, retired_op_ref text,
                 created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            """CREATE TABLE governance_floor_audit (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 floor_id bigint REFERENCES governance_floor(id),
                 event text NOT NULL, actor text NOT NULL, op_ref text NOT NULL,
                 at timestamptz NOT NULL DEFAULT now(), note text
               )"""
        )
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql, enforced_in_code) "
            "VALUES ('No work_item sits abandoned past 72h.', 'op#32730', 'SELECT true', true) RETURNING id"
        )
        floor_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref) "
            "VALUES (%s, 'checked_pass', 'governance_floor_reconcile.py', 'op#32730')",
            (floor_id,),
        )
        _make_substrate_ledger_row(cur)
    return pg_dsn


@pytest.fixture
def floor_db_not_substrate(floor_db, pg_dsn):
    """Same floor rows, but no migration_ledger row for 131 -- a genuine
    non-substrate (test/harness) DSN, the case the canonical-path write
    must refuse and --out must still be allowed to bypass."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM migration_ledger WHERE migration_name = '131_governance_floor.sql'")
    return pg_dsn


@pytest.fixture
def floor_db_with_retired(floor_db, pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql, retired_at, retired_op_ref) "
            "VALUES ('A lifted floor.', 'op#1', 'SELECT true', now(), 'op#99')"
        )
    return pg_dsn


@pytest.fixture
def doc_file(tmp_path, monkeypatch):
    doc = tmp_path / "governance-floors.md"
    doc.write_text(
        "# Doctrine prose above\n\n"
        "### Floor rows (generated)\n\n"
        "<!-- GENERATED:governance_floor:START -->\n"
        "stale placeholder\n"
        "<!-- GENERATED:governance_floor:END -->\n\n"
        "## 2. Org-chart\nProse below, must survive untouched.\n"
    )
    monkeypatch.setattr(ggf, "DOC_PATH", str(doc))
    return doc


def test_render_includes_n_of_m_passing_line(floor_db, doc_file):
    assert ggf.main(["--dsn", floor_db]) == 0
    text = doc_file.read_text()
    assert "1 of 1 floors passing" in text


def test_render_includes_floor_sentence_and_op_ref(floor_db, doc_file):
    ggf.main(["--dsn", floor_db])
    text = doc_file.read_text()
    assert "No work_item sits abandoned past 72h." in text
    assert "op#32730" in text


def test_prose_outside_markers_untouched_byte_for_byte(floor_db, doc_file):
    before = doc_file.read_text()
    pre_prose, _, rest = before.partition("<!-- GENERATED:governance_floor:START -->")
    _, _, post_prose = rest.partition("<!-- GENERATED:governance_floor:END -->")

    ggf.main(["--dsn", floor_db])

    after = doc_file.read_text()
    after_pre, _, after_rest = after.partition("<!-- GENERATED:governance_floor:START -->")
    _, _, after_post = after_rest.partition("<!-- GENERATED:governance_floor:END -->")
    assert after_pre == pre_prose
    assert after_post == post_prose


def test_missing_markers_refuses(floor_db, doc_file):
    doc_file.write_text("no markers here at all")
    assert ggf.main(["--dsn", floor_db]) == 1


def test_no_floor_rows_still_renders_zero_of_zero(pg_dsn, doc_file):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        cur.execute(
            """CREATE TABLE governance_floor (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 sentence text NOT NULL, op_ref text NOT NULL,
                 predicate_sql text NOT NULL, enforced_in_code boolean NOT NULL DEFAULT false,
                 decision_ref text, retired_at timestamptz, retired_op_ref text,
                 created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            """CREATE TABLE governance_floor_audit (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 floor_id bigint REFERENCES governance_floor(id),
                 event text NOT NULL, actor text NOT NULL, op_ref text NOT NULL,
                 at timestamptz NOT NULL DEFAULT now(), note text
               )"""
        )
        _make_substrate_ledger_row(cur)
    assert ggf.main(["--dsn", pg_dsn]) == 0
    assert "0 of 0 floors passing" in doc_file.read_text()


# ── Retirable floors (console bus #80595) ─────────────────────────────────
def test_retired_floor_excluded_from_main_table_and_count(floor_db_with_retired, doc_file):
    ggf.main(["--dsn", floor_db_with_retired])
    text = doc_file.read_text()
    assert "1 of 1 floors passing" in text
    assert "A lifted floor." not in text.split("Retired floors")[0]


def test_retired_floor_listed_separately_with_op_ref(floor_db_with_retired, doc_file):
    ggf.main(["--dsn", floor_db_with_retired])
    text = doc_file.read_text()
    assert "Retired floors" in text
    assert "op#1" in text
    assert "op#99" in text


# ── A floor with no checked_pass/checked_fail row must never render PASS
# (console bus #80659: PR#608's doc shipped "PASS" before migration 131 ever
# touched the real substrate or any check had run -- root cause was running
# this generator against the ephemeral test harness and committing that
# output into the real docs file, not a generator defect, but pin the
# never-silently-PASS behavior with a direct test regardless). ────────────
def test_floor_with_zero_audit_rows_renders_not_yet_checked_never_pass(pg_dsn, doc_file):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        cur.execute(
            """CREATE TABLE governance_floor (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 sentence text NOT NULL, op_ref text NOT NULL,
                 predicate_sql text NOT NULL, enforced_in_code boolean NOT NULL DEFAULT false,
                 decision_ref text, retired_at timestamptz, retired_op_ref text,
                 created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            """CREATE TABLE governance_floor_audit (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 floor_id bigint REFERENCES governance_floor(id),
                 event text NOT NULL, actor text NOT NULL, op_ref text NOT NULL,
                 at timestamptz NOT NULL DEFAULT now(), note text
               )"""
        )
        # No INSERT INTO governance_floor_audit at all -- this floor has never
        # been checked, by anything, ever.
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('never checked yet', 'op#1', 'SELECT true')"
        )
        _make_substrate_ledger_row(cur)
    assert ggf.main(["--dsn", pg_dsn]) == 0
    text = doc_file.read_text()
    assert "not yet checked" in text
    assert "PASS" not in text
    assert "0 of 1 floors passing" in text


# ── Must refuse to write the canonical path off a non-substrate DSN
# (console bus #80664); --out is the escape hatch for harness/test runs ────
def test_refuses_canonical_write_on_non_substrate_dsn(floor_db_not_substrate, doc_file):
    before = doc_file.read_text()
    assert ggf.main(["--dsn", floor_db_not_substrate]) == 1
    assert doc_file.read_text() == before  # untouched


def test_out_path_bypasses_the_substrate_check(floor_db_not_substrate, doc_file, tmp_path):
    out = tmp_path / "harness-render.md"
    out.write_text(
        "<!-- GENERATED:governance_floor:START -->\nold\n<!-- GENERATED:governance_floor:END -->\n"
    )
    assert ggf.main(["--dsn", floor_db_not_substrate, "--out", str(out)]) == 0
    assert "1 of 1 floors passing" in out.read_text()
    assert out.read_text() != "old"
    # the canonical path (doc_file) is untouched by an --out run
    assert "1 of 1 floors passing" not in doc_file.read_text()


def test_out_path_still_refuses_without_markers(floor_db_not_substrate, doc_file, tmp_path):
    out = tmp_path / "harness-render.md"
    out.write_text("no markers here at all")
    assert ggf.main(["--dsn", floor_db_not_substrate, "--out", str(out)]) == 1


# ── Source line (console bus #80664): the generated block must name where
# the PASS/FAIL came from, so a reader never mistakes test-DB content for a
# real substrate check (the exact failure mode in #80659). ─────────────────
def test_source_line_names_the_substrate_when_writing_canonically(floor_db, doc_file):
    ggf.main(["--dsn", floor_db])
    text = doc_file.read_text()
    assert ggf.SUBSTRATE_SILO_REF in text


def test_source_line_warns_non_substrate_when_writing_via_out(floor_db_not_substrate, doc_file, tmp_path):
    out = tmp_path / "harness-render.md"
    out.write_text(
        "<!-- GENERATED:governance_floor:START -->\nold\n<!-- GENERATED:governance_floor:END -->\n"
    )
    ggf.main(["--dsn", floor_db_not_substrate, "--out", str(out)])
    text = out.read_text()
    assert "NON-SUBSTRATE" in text or "non-substrate" in text.lower()
