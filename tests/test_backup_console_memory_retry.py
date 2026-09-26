"""backup_console_memory._connect_with_retry: a transient pooler-DNS blip is retried and
recovers; a persistent connect failure STILL fails loud (re-raises) — retries never turn a
failure into a silent pass (Nazim #43430, bus #43426)."""
import pytest

from scripts import backup_console_memory as B


class _FakeConn:
    pass


def test_connect_retries_transient_blip_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_connect(dsn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise B.psycopg.OperationalError(
                "could not translate host name aws-1-ap-southeast-2.pooler.supabase.com")
        return _FakeConn()

    monkeypatch.setattr(B.psycopg, "connect", fake_connect)
    conn = B._connect_with_retry("dsn", attempts=3, backoff_s=0, sleep=lambda s: None)
    assert isinstance(conn, _FakeConn)
    assert calls["n"] == 2, "must retry the transient blip, then succeed"


def test_connect_persistent_failure_reraises_loud(monkeypatch):
    calls = {"n": 0}

    def fake_connect(dsn):
        calls["n"] += 1
        raise B.psycopg.OperationalError("persistent outage")

    monkeypatch.setattr(B.psycopg, "connect", fake_connect)
    with pytest.raises(B.psycopg.OperationalError):
        B._connect_with_retry("dsn", attempts=3, backoff_s=0, sleep=lambda s: None)
    assert calls["n"] == 3, "must try the full budget then RE-RAISE (fail loud, never silent pass)"
