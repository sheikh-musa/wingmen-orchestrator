"""Tests for scripts/migration_rls_grant_lint.py — the STATIC, no-DB migration-diff
lint for the RLS-UPDATE-without-column-GRANT gap + lifecycle value-adds (PB-03).

Pure text classifier: no DB, no network, no DATABASE_URL — so these run anywhere,
unlike the live-DB tests/test_rls_grant_lint.py this lint COMPLEMENTS. Vectors live
in scripts/migration_rls_grant_lint_vectors.json so the examples are inspectable.
"""
import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LINT = _ROOT / "scripts" / "migration_rls_grant_lint.py"
_VECTORS = _ROOT / "scripts" / "migration_rls_grant_lint_vectors.json"


def _load():
    spec = importlib.util.spec_from_file_location("migration_rls_grant_lint", _LINT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: the @dataclass decorator resolves string annotations via
    # sys.modules[__name__] on py3.9 — without this, importlib load raises
    # AttributeError: 'NoneType' object has no attribute '__dict__'.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


def _codes(sql):
    return sorted(f.code for f in m.scan_sql(sql))


def test_vectors_file_matches():
    vectors = json.loads(_VECTORS.read_text())
    assert vectors, "vectors file is empty"
    for v in vectors:
        assert _codes(v["sql"]) == sorted(v["expect_codes"]), f"vector {v['name']!r} mismatch"


def test_the_core_bug_is_flagged():
    # cosem PR#429 shape: an UPDATE policy with no column-level GRANT.
    sql = "CREATE POLICY p ON t FOR UPDATE TO authenticated USING (true);"
    assert _codes(sql) == ["RLS_UPDATE_NO_COLUMN_GRANT"]


def test_column_grant_clears_it():
    sql = ("GRANT UPDATE (a, b) ON t TO authenticated; "
           "CREATE POLICY p ON t FOR UPDATE TO authenticated USING (true);")
    assert _codes(sql) == []


def test_select_only_policy_not_flagged():
    assert _codes("CREATE POLICY p ON t FOR SELECT TO authenticated USING (true);") == []


def test_no_for_clause_defaults_to_all_and_is_flagged():
    assert _codes("CREATE POLICY p ON t TO authenticated USING (true);") == ["RLS_UPDATE_NO_COLUMN_GRANT"]


def test_whole_table_grant_is_not_a_column_grant():
    # A table-level GRANT UPDATE (no column list) does NOT bound columns.
    sql = "GRANT UPDATE ON t TO authenticated; CREATE POLICY p ON t FOR UPDATE USING (true);"
    assert _codes(sql) == ["RLS_UPDATE_NO_COLUMN_GRANT"]


def test_comment_is_ignored():
    sql = "-- CREATE POLICY p ON t FOR UPDATE USING (true);\nSELECT 1;"
    assert _codes(sql) == []


def test_schema_qualified_quoted_table_correlates():
    sql = ('GRANT UPDATE (s) ON public."A" TO authenticated; '
           'CREATE POLICY p ON public."A" FOR UPDATE USING (true);')
    assert _codes(sql) == []


def test_enum_add_value_sweep():
    assert _codes("ALTER TYPE st ADD VALUE 'in_progress';") == ["LIFECYCLE_VALUE_ADD"]


def test_nonterminal_default_sweep():
    assert _codes("ALTER TABLE t ADD COLUMN status text DEFAULT 'draft';") == ["LIFECYCLE_NONTERMINAL_DEFAULT"]


def test_warn_exit_code_is_advisory_by_default():
    # default: findings -> exit 0 (WARN); --strict -> exit 1.
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write("CREATE POLICY p ON t FOR UPDATE USING (true);")
        path = fh.name
    try:
        assert m.main(["prog", path]) == 0
        assert m.main(["prog", "--strict", path]) == 1
    finally:
        os.unlink(path)
