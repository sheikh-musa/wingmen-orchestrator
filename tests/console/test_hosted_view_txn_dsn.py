"""Transaction-pooler DSN for the hosted clone's one-shot reads (op#<console-
pooler-fix>, orch-console #86539): console_readonly's session-mode pool hit its
15-slot ceiling (cc-console-revamp-1 #86533). Every hosted-clone caller opens
one connection, runs one or more SELECTs inside the single implicit
transaction, and closes -- no session state held across requests -- so it
should use the transaction-mode pooler port (6543), not session-mode (5432).
"""
from nervous_system.console import hosted_view as hv


def test_txn_dsn_swaps_session_port_for_transaction_port(monkeypatch):
    monkeypatch.setenv("CONSOLE_DB_URL", "postgresql://ro@aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dsn = hv._txn_dsn()
    assert "port=6543" in dsn
    assert "port=5432" not in dsn


def test_txn_dsn_preserves_host_and_user(monkeypatch):
    monkeypatch.setenv("CONSOLE_DB_URL", "postgresql://ro@aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dsn = hv._txn_dsn()
    assert "host=aws-1-ap-southeast-2.pooler.supabase.com" in dsn
    assert "user=ro" in dsn
    assert "dbname=postgres" in dsn


def test_txn_dsn_falls_back_to_database_url_like_dsn_does(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://rw@h:5432/db")
    dsn = hv._txn_dsn()
    assert "port=6543" in dsn
    assert "host=h" in dsn


def test_txn_dsn_raises_when_no_dsn_configured(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        hv._txn_dsn()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
