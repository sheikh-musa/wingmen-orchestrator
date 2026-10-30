"""scripts/cc_cost_snapshot.py — the read-only API-equivalent $ spend view
over cc_session_costs (SRE §4.6).

Two kinds of test, no database ever contacted (local or remote):

- `_cost()` is a pure function — exercised directly with hand-computed
  expectations against the module's own `RATES["claude-opus-4-8"]`.
- `main()` is driven with `psycopg2.connect` monkeypatched at the module
  boundary, so the connect/cursor/execute/fetchall sequence runs exactly as
  the real script drives it but against a canned in-memory result set. The
  `DATABASE_URL`-unset path is checked separately: it must bail (rc 2) BEFORE
  any connect attempt.
"""
import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "cc_cost_snapshot.py")

_spec = importlib.util.spec_from_file_location("cc_cost_snapshot", _SCRIPT)
cc_cost_snapshot = importlib.util.module_from_spec(_spec)
sys.modules["cc_cost_snapshot"] = cc_cost_snapshot
_spec.loader.exec_module(cc_cost_snapshot)

# Clearly-fake placeholder — `connect` is mocked, so this is never dialed.
FAKE_DATABASE_URL = "fake-dsn-cc-cost-snapshot-never-dialed"

RATES = cc_cost_snapshot.RATES["claude-opus-4-8"]


# ── _cost(): pure function ───────────────────────────────────────────────

def test_cost_all_four_counts_hand_computed():
    # 1M of each token kind -> each rate contributes its $/MTok directly.
    # (1e6*5 + 1e6*25 + 1e6*6.25 + 1e6*0.5) / 1e6 = 5 + 25 + 6.25 + 0.5
    row = (1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert cc_cost_snapshot._cost(row, RATES) == pytest.approx(36.75)


def test_cost_treats_none_as_zero():
    # Only output + cache-read populated; input and cache-write are None.
    # (0 + 2e6*25 + 0 + 4e6*0.5) / 1e6 = 50 + 2 = 52
    row = (None, 2_000_000, None, 4_000_000)
    assert cc_cost_snapshot._cost(row, RATES) == pytest.approx(52.0)


def test_cost_all_zero_is_zero():
    assert cc_cost_snapshot._cost((0, 0, 0, 0), RATES) == 0.0


# ── main(): DATABASE_URL unset → bail before connecting ──────────────────

def test_main_without_database_url_returns_2_and_never_connects(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def _boom(*a, **k):  # must not be reached
        raise AssertionError("psycopg2.connect called despite no DATABASE_URL")

    monkeypatch.setattr(cc_cost_snapshot.psycopg2, "connect", _boom)
    monkeypatch.setattr(sys, "argv", ["cc_cost_snapshot.py"])

    rc = cc_cost_snapshot.main()

    assert rc == 2
    assert "could-not-measure" in capsys.readouterr().err


# ── main(): happy path with psycopg2.connect mocked ──────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_db(monkeypatch):
    """Install a fake psycopg2.connect returning a canned per-body result set.

    The per-body query yields 6-tuples:
    (cc_identity, sum_in, sum_out, sum_cache_creation, sum_cache_read, count).
    """
    # Two bodies. In the real query order (ORDER BY cc_identity) the
    # alphabetically-first row is the CHEAP one — so a correct main() must
    # re-sort descending by cost and print the expensive body first.
    rows = [
        ("aaa-cheap-body", 1_000, 1_000, 1_000, 1_000, 3),
        ("zzz-pricey-body", 1_000_000, 1_000_000, 1_000_000, 1_000_000, 42),
    ]
    conn = _FakeConn(rows)

    def _fake_connect(dsn, *a, **k):
        _fake_connect.dsn = dsn
        return conn

    _fake_connect.dsn = None
    monkeypatch.setattr(cc_cost_snapshot.psycopg2, "connect", _fake_connect)
    monkeypatch.setenv("DATABASE_URL", FAKE_DATABASE_URL)
    monkeypatch.setattr(sys, "argv", ["cc_cost_snapshot.py"])
    return conn, _fake_connect


def test_main_returns_zero_and_sorts_bodies_by_cost_descending(fake_db, capsys):
    conn, connect = fake_db

    rc = cc_cost_snapshot.main()

    assert rc == 0
    assert connect.dsn == FAKE_DATABASE_URL  # the fake DSN, never a real one

    out = capsys.readouterr().out
    i_pricey = out.index("zzz-pricey-body")
    i_cheap = out.index("aaa-cheap-body")
    # Expensive body printed BEFORE the cheap one despite reverse alpha order.
    assert i_pricey < i_cheap
