"""scripts/asks_triage.py — migration 082's triage CLI (bus #45557 GO,
sibling of asks_open.py/asks_close.py). Round-trip against the ephemeral
operator_ledger_db fixture (tests/conftest.py) — never production."""
import importlib

import pytest

at = importlib.import_module("scripts.asks_triage")


# ── pure validation ────────────────────────────────────────────────────────
def test_ask_without_summary_raises():
    with pytest.raises(ValueError):
        at.triage(1, "ask", summary=None, dsn="postgresql://unused")


def test_ask_with_blank_summary_raises():
    with pytest.raises(ValueError):
        at.triage(1, "ask", summary="   ", dsn="postgresql://unused")


# ── CLI: build_parser shape ────────────────────────────────────────────────
def test_parser_accepts_ask_action():
    args = at.build_parser().parse_args(
        ["12", "ask", "--summary", "one line", "--delegated-to", "cc-scholar"]
    )
    assert args.id == 12
    assert args.action == "ask"
    assert args.summary == "one line"
    assert args.delegated_to == "cc-scholar"


def test_parser_rejects_unknown_action():
    with pytest.raises(SystemExit):
        at.build_parser().parse_args(["1", "bogus"])


# ── round-trip against the ephemeral harness ─────────────────────────────────
def _insert_captured(conn, ask="[__test__] captured row"):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO operator_asks (ask) VALUES (%s) RETURNING id", (ask,))
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def test_triage_ask_sets_state_summary_and_clears_closed(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(rid, "ask", summary="a clean one-liner", delegated_to="cc-cosem-platform")
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, triage_summary, delegated_to, closed_at, triaged_by "
            "FROM operator_asks WHERE id=%s", (rid,),
        )
        triage_state, summary, delegated_to, closed_at, triaged_by = cur.fetchone()
    assert triage_state == "ask"
    assert summary == "a clean one-liner"
    assert delegated_to == "cc-cosem-platform"
    assert closed_at is None
    assert triaged_by is not None


def test_triage_ask_reopens_a_closed_row():
    pass  # covered by test_triage_ask_undoes_a_heuristic_not_an_ask below


def test_triage_ask_undoes_a_heuristic_not_an_ask(operator_ledger_db):
    """The heuristic pre-classifier's undo path: 'ask' ALWAYS clears
    closed_at, even on a row that a prior triage/heuristic pass closed."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c, "thanks")
    n0 = at.triage(rid, "not")
    assert n0 == 1
    n1 = at.triage(rid, "ask", summary="actually this WAS a real request")
    assert n1 == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, closed_at, triage_summary FROM operator_asks WHERE id=%s", (rid,)
        )
        triage_state, closed_at, summary = cur.fetchone()
    assert triage_state == "ask"
    assert closed_at is None
    assert summary == "actually this WAS a real request"


def test_triage_not_closes_with_not_a_request_reason(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(rid, "not")
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, closed_at, closed_reason FROM operator_asks WHERE id=%s", (rid,)
        )
        triage_state, closed_at, closed_reason = cur.fetchone()
    assert triage_state == "not_an_ask"
    assert closed_at is not None
    assert closed_reason == "not_a_request"


def test_triage_done_closes_with_evidence(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(rid, "done", evidence="am#12345")
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, closed_at, closed_reason, triage_evidence_ref "
            "FROM operator_asks WHERE id=%s", (rid,)
        )
        triage_state, closed_at, closed_reason, evidence_ref = cur.fetchone()
    assert triage_state == "done"
    assert closed_at is not None
    assert closed_reason == "done"
    assert evidence_ref == "am#12345"


def test_triage_not_on_already_closed_row_updates_zero(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    at.triage(rid, "not")
    n = at.triage(rid, "not")  # already closed_at IS NOT NULL now
    assert n == 0


def test_triage_unknown_id_updates_zero(operator_ledger_db):
    n = at.triage(999999999, "not")
    assert n == 0


# ── main(): exit codes ────────────────────────────────────────────────────────
def test_main_returns_2_on_missing_summary(capsys):
    rc = at.main(["1", "ask"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "error:" in captured.err


def test_main_prints_ok_and_returns_0_on_success(operator_ledger_db, capsys):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    rc = at.main([str(rid), "done", "--evidence", "am#1"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == "ok"
