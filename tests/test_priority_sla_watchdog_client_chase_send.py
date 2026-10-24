"""Regression test for the 2026-10-15 hc#312 arm-time crash: created_epoch comes
back from psycopg2's extract(epoch FROM created_at) as decimal.Decimal, never a
float, so `time.time() - created_epoch` raised TypeError the first time
_send_client_ask_chase was ever actually called (chase_client_asks skips the
send entirely in dry mode, so a 60h observe-only soak never exercised this path)."""
from decimal import Decimal

from scripts import priority_sla_watchdog as s


class _FakeCursor:
    def __init__(self, executed):
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.executed)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_send_client_ask_chase_accepts_decimal_created_epoch():
    # _send_client_ask_chase posts via the RPC on its OWN injected conn (a deliberate
    # DI seam, backlog#124/bus #74939), not bus_send.send() -- observe conn.executed.
    conn = _FakeConn()
    row = {
        "id": 654,
        "ask": "example client ask",
        "created_epoch": Decimal("1791702706.675551"),  # real shape from extract(epoch FROM ...)
        "committed_date": None,
    }
    ok = s._send_client_ask_chase(conn, "cosem-port", row)
    assert ok is True
    # one post to the owning lane only -- console is no longer CC'd per-ask,
    # it gets a periodic summary instead (bus#67841/#67965, same day)
    posts = [p for sql, p in conn.executed if "post_agent_message" in sql]
    assert len(posts) == 1
    assert posts[0][0] == "cosem-port"
    assert conn.committed is True


def test_send_client_ask_chase_handles_missing_created_epoch():
    conn = _FakeConn()
    row = {"id": 999, "ask": "no created_epoch on this row", "created_epoch": None, "committed_date": None}
    ok = s._send_client_ask_chase(conn, "orch-console", row)
    assert ok is True
