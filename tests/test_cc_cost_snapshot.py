"""scripts/cc_cost_snapshot.py — API-equivalent $ snapshot (SRE §4.6 spend view).

_cost() is pure, so its cases need nothing but hand-computed literals. main()'s
only external contact is psycopg2.connect; the DB cases replace it with a fake
connection/cursor (mirrors tests/test_bus_send.py's _FakeCursor/_FakeConn) so no
test ever opens a real database.
"""
import sys

import psycopg2

from scripts import cc_cost_snapshot as snapshot

RATES = snapshot.RATES["claude-opus-4-8"]

# Clearly fake placeholder — psycopg2.connect is always replaced before main() runs.
FAKE_DSN = "fake-database-url-never-connected"


# ── _cost: pure calculation ──────────────────────────────────────────────────
def test_cost_applies_each_rate_to_its_own_token_count():
    # 2M in @ $5.00 + 1M out @ $25.00 + 4M cache-write @ $6.25
    # + 10M cache-read @ $0.50 = 10 + 25 + 25 + 5 = $65.00
    assert snapshot._cost((2_000_000, 1_000_000, 4_000_000, 10_000_000), RATES) == 65.0


def test_cost_treats_none_token_counts_as_zero():
    # in/cache-write None -> 0; out 2M @ $25 + cache-read 4M @ $0.50
    # = 50 + 2 = $52.00 (and must not raise on the None operands)
    assert snapshot._cost((None, 2_000_000, None, 4_000_000), RATES) == 52.0


def test_cost_of_all_zero_counts_is_zero():
    assert snapshot._cost((0, 0, 0, 0), RATES) == 0.0


# ── main(): no DATABASE_URL — fail loud, never fabricate a report ────────────
def test_main_without_database_url_returns_2_and_never_connects(monkeypatch, capsys):
    def _boom(*_a, **_k):
        raise AssertionError("main() must not connect without DATABASE_URL")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(psycopg2, "connect", _boom)
    monkeypatch.setattr(sys, "argv", ["cc_cost_snapshot.py"])

    assert snapshot.main() == 2
    captured = capsys.readouterr()
    assert "could-not-measure" in captured.err
    assert captured.out == ""


# ── main(): mocked DB — the per-body table is cost-sorted descending ────────
class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return self._cur

    def close(self):
        self.closed = True


def test_main_prints_bodies_sorted_by_cost_descending(monkeypatch, capsys):
    # Aggregate rows arrive in the query's ORDER BY cc_identity (cheap, mid,
    # pricey), so only main()'s sort can put the expensive body first.
    # Hand-computed costs: cheap $3.00, mid $21.75, pricey $55.00.
    rows = [
        ("cc-cheap", 100_000, 100_000, 0, 0, 2),
        ("cc-mid", 500_000, 500_000, 1_000_000, 1_000_000, 3),
        ("cc-pricey", 1_000_000, 2_000_000, 0, 0, 1),
    ]
    conn = _FakeConn(_FakeCursor(rows))

    def fake_connect(dsn):
        assert dsn == FAKE_DSN, "main() must connect with DATABASE_URL"
        return conn

    monkeypatch.setenv("DATABASE_URL", FAKE_DSN)
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    monkeypatch.setattr(sys, "argv", ["cc_cost_snapshot.py"])

    assert snapshot.main() == 0
    out = capsys.readouterr().out
    assert conn.closed
    assert out.index("cc-pricey") < out.index("cc-mid") < out.index("cc-cheap")
