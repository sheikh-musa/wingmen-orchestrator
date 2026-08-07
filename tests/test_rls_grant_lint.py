"""Integration tests for scripts/rls_grant_lint.py against the live substrate.

Asserts the post-031 invariants the lint is meant to guarantee (CAI-RESP-511):
no anon/auth/PUBLIC TRUNCATE grant survives, cai's 5 open-write policies are
closed, the postgres-owned default no longer grants anon write, and the ui_events
telemetry allowlist demotes its anon-write from CRITICAL to WARN.

Skips cleanly when DATABASE_URL is unset (same pattern as the other DB-touching
suites) so CI without a substrate connection stays green.
"""
import importlib.util
import os
from pathlib import Path

import pytest

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="no DATABASE_URL/SUPABASE_DB_URL — substrate-connected test")

_LINT = Path(__file__).resolve().parent.parent / "scripts" / "rls_grant_lint.py"


def _load_lint():
    spec = importlib.util.spec_from_file_location("rls_grant_lint", _LINT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def findings():
    import psycopg

    lint = _load_lint()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        return lint.collect_findings(cur)


def _by_code(findings, code):
    return [f for f in findings if f["code"] == code]


def test_no_truncate_grants_survive(findings):
    # 031 revoked TRUNCATE from anon/auth/PUBLIC on every table.
    assert _by_code(findings, "TRUNCATE_GRANT") == []


def test_no_open_write_policies_except_allowlisted(findings):
    # cai's 5-policy fix closed every USING(true)/WITH CHECK(true) write policy;
    # only the allowlisted ui_events telemetry policy remains (as WARN).
    assert _by_code(findings, "OPEN_WRITE_POLICY") == []
    allowlisted = _by_code(findings, "ANON_WRITE_POLICY_ALLOWLISTED")
    assert all(f["table"] == "ui_events" for f in allowlisted)


def test_postgres_default_privs_hardened(findings):
    # 031's ALTER DEFAULT PRIVILEGES removed anon write from the postgres-owned default.
    assert _by_code(findings, "DEFAULT_ANON_WRITE") == []


def test_ui_events_allowlisted_not_critical(findings):
    # ui_events anon-write is deliberate telemetry: WARN, never CRITICAL.
    crit_tables = {f["table"] for f in findings if f["severity"] == "CRITICAL"}
    assert "ui_events" not in crit_tables


def test_supabase_admin_residual_is_warn_only(findings):
    # The platform-owned default ACL is out of SQL reach — surfaced as WARN, never a hard fail.
    residual = _by_code(findings, "DEFAULT_ANON_WRITE_RESIDUAL")
    assert all(f["severity"] == "WARN" for f in residual)
