"""Tests for scripts/data_truth.py — the fail-safe classification contract
(orch-console gate condition #2, bus #51717): an unknown/unclassified
project/org returns UNCLASSIFIED, and every consumer treats that as REAL
(treat_as_real() is True). Pure unit tests against _from_row(); no DB."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import data_truth as dt  # noqa: E402


def test_unregistered_is_unclassified_not_a_guess():
    c = dt._from_row("ywrpttpxwfcoodovxhsr", "deadbeef", None)
    assert c.registered is False
    assert c.classification == dt.UNCLASSIFIED


def test_unclassified_is_treated_as_real():
    c = dt._from_row("ywrpttpxwfcoodovxhsr", "deadbeef", None)
    assert c.treat_as_real() is True


def test_unclassified_needs_a_second_look():
    c = dt._from_row("ywrpttpxwfcoodovxhsr", "deadbeef", None)
    assert c.needs_a_second_look() is True


def test_registered_real_is_treated_as_real():
    row = ("REAL", "evidence text", "owner", "alias", None)
    c = dt._from_row("ceayjeamtmcyzzvqflus", "", row)
    assert c.registered is True
    assert c.treat_as_real() is True
    assert c.needs_a_second_look() is False


def test_registered_synthetic_is_not_treated_as_real():
    row = ("SYNTHETIC", "evidence text", "owner", "alias", None)
    c = dt._from_row("ywrpttpxwfcoodovxhsr", "ba98da04", row)
    assert c.registered is True
    assert c.treat_as_real() is False
    assert c.needs_a_second_look() is False


def test_mixed_pending_real_is_treated_as_real_and_flagged():
    row = ("MIXED_PENDING_REAL", "evidence text", "owner", "alias", None)
    c = dt._from_row("ywrpttpxwfcoodovxhsr", "1478c9b2", row)
    assert c.treat_as_real() is True
    assert c.needs_a_second_look() is True


def test_mixed_is_treated_as_real_and_flagged():
    row = ("MIXED", "evidence text", "owner", "alias", None)
    c = dt._from_row("ywrpttpxwfcoodovxhsr", "", row)
    assert c.treat_as_real() is True
    assert c.needs_a_second_look() is True


def test_cli_classify_unregistered_exits_nonzero(capsys):
    import io
    import psycopg

    class _FakeCursor:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    orig_connect = psycopg.connect
    psycopg.connect = lambda *a, **k: _FakeConn()
    orig_dburl = dt.bus_send.dburl
    dt.bus_send.dburl = lambda env: "postgresql://fake"
    try:
        rc = dt.main(["classify", "ywrpttpxwfcoodovxhsr", "deadbeef"])
    finally:
        psycopg.connect = orig_connect
        dt.bus_send.dburl = orig_dburl
    out = capsys.readouterr().out
    assert rc == 1
    assert "UNCLASSIFIED" in out
    assert "treat_as_real()=True" in out
