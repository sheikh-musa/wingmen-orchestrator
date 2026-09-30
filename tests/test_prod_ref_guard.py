"""Prod-ref refusal guard (root conftest.py) — pure-function proof (backlog#68).

The guard itself (pytest_configure -> pytest.exit) can't be asserted in-process without
aborting this session, so its DECISION logic (resolve_dsns / prod_ref_in) is pure and
tested here. A prod DSN -> the ref is named (session would refuse); a local/ephemeral
DSN or no DSN -> None (session proceeds, DSN tests skip as before).
"""
import importlib.util
import os
from pathlib import Path

# import the root conftest.py as a module (it's not on the normal import path)
_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_root_conftest", _ROOT / "conftest.py")
_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conftest)


def test_prod_substrate_dsn_is_refused():
    dsn = "postgresql://u:p@aws-1.pooler.supabase.com:5432/postgres?options=project%3Dtscuymavysscrvoberrr"
    assert _conftest.prod_ref_in([dsn]) == "tscuymavysscrvoberrr"


def test_ihsanos_mt_and_irsyad_silo_refused():
    assert _conftest.prod_ref_in(["postgresql://x@h/ceayjeamtmcyzzvqflus"]) == "ceayjeamtmcyzzvqflus"
    assert _conftest.prod_ref_in(["postgresql://x@h/goumlynecruxrlmzlntp"]) == "goumlynecruxrlmzlntp"


def test_localhost_ephemeral_dsn_allowed():
    assert _conftest.prod_ref_in(["postgresql://postgres:postgres@localhost:5432/wingmen_test"]) is None
    assert _conftest.prod_ref_in(["postgresql:///wingmen?host=/tmp/pg-abc123"]) is None


def test_no_dsn_allowed():
    assert _conftest.prod_ref_in([]) is None


def test_resolve_dsns_reads_env_and_skips_missing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/test")
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    got = _conftest.resolve_dsns(include_env_file=False)
    assert got == ["postgresql://x@localhost/test"]


def test_resolve_then_prod_ref_end_to_end(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@h/db?project=tscuymavysscrvoberrr")
    assert _conftest.prod_ref_in(_conftest.resolve_dsns(include_env_file=False)) == "tscuymavysscrvoberrr"
