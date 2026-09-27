"""scripts/asks_open.py — piece 3 of op#22669. Empty-ask validation is pure;
the INSERT itself is exercised live (same convention as
test_operator_log_failure_reason.py) so a schema mismatch fails the suite."""
import importlib
import os

import pytest

ao = importlib.import_module("scripts.asks_open")


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


# ── pure validation ───────────────────────────────────────────────────────────
def test_empty_ask_raises():
    with pytest.raises(ValueError):
        ao.open_ask("", dsn="postgresql://unused")


def test_whitespace_only_ask_raises():
    with pytest.raises(ValueError):
        ao.open_ask("   ", dsn="postgresql://unused")


def test_no_dsn_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setattr(ao, "_dsn", lambda: None)
    with pytest.raises(RuntimeError):
        ao.open_ask("a real ask", dsn=None)


# ── CLI: build_parser shape ───────────────────────────────────────────────────
def test_parser_accepts_all_flags():
    args = ao.build_parser().parse_args(
        ["the ask", "--chase-hours", "6", "--outbound-msg-id", "42", "--delegated-to", "cc-scholar"]
    )
    assert args.ask == "the ask"
    assert args.chase_hours == 6.0
    assert args.outbound_msg_id == 42
    assert args.delegated_to == "cc-scholar"


def test_parser_defaults_are_none():
    args = ao.build_parser().parse_args(["the ask"])
    assert args.chase_hours is None
    assert args.outbound_msg_id is None
    assert args.delegated_to is None


# ── live round-trip ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _dsn(), reason="no DSN (offline CI) — the pure tests cover validation")
def test_open_ask_inserts_with_waiting_on_operator_true():
    import psycopg
    rid = ao.open_ask("[__test__] asks_open round-trip", delegated_to="__test_body__")
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute(
                "SELECT ask, delegated_to, waiting_on_operator, chase_by, outbound_msg_id, closed_at "
                "FROM operator_asks WHERE id=%s", (rid,)
            )
            ask, delegated_to, waiting, chase_by, outbound_id, closed_at = cur.fetchone()
        assert ask == "[__test__] asks_open round-trip"
        assert delegated_to == "__test_body__"
        assert waiting is True
        assert chase_by is None
        assert outbound_id is None
        assert closed_at is None
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_open_ask_computes_chase_by_from_hours():
    import psycopg
    rid = ao.open_ask("[__test__] chase-hours round-trip", chase_hours=6.0,
                      delegated_to="__test_body__")
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute(
                "SELECT extract(epoch FROM (chase_by - now())) FROM operator_asks WHERE id=%s", (rid,)
            )
            (delta_seconds,) = cur.fetchone()
        # allow generous slack for round-trip latency; must be close to 6h
        assert 6 * 3600 - 60 <= delta_seconds <= 6 * 3600 + 60
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_open_ask_links_outbound_msg_id():
    import psycopg
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, text, delivered, tg_message_id) "
            "VALUES ('outbound','telegram','[__test__] linked outbound',true,313131) RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        c.commit()
    rid = None
    try:
        rid = ao.open_ask("[__test__] linked ask", outbound_msg_id=outbound_id,
                          delegated_to="__test_body__")
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT outbound_msg_id FROM operator_asks WHERE id=%s", (rid,))
            (got,) = cur.fetchone()
        assert got == outbound_id
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            if rid is not None:
                cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (outbound_id,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_open_ask_defaults_delegated_to_env_or_orch_console(monkeypatch):
    import psycopg
    monkeypatch.delenv("ORCH_AGENT_ID", raising=False)
    rid = ao.open_ask("[__test__] default delegated_to")
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT delegated_to FROM operator_asks WHERE id=%s", (rid,))
            (delegated_to,) = cur.fetchone()
        assert delegated_to == "orch-console"
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()


# ── main(): exit codes ────────────────────────────────────────────────────────
def test_main_returns_2_and_prints_error_on_empty_ask(capsys):
    rc = ao.main([""])
    captured = capsys.readouterr()
    assert rc == 2
    assert "error:" in captured.err


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_main_prints_id_and_returns_0_on_success(capsys):
    import psycopg
    rc = ao.main(["[__test__] main() round-trip", "--delegated-to", "__test_body__"])
    captured = capsys.readouterr()
    assert rc == 0
    rid = int(captured.out.strip())
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT ask FROM operator_asks WHERE id=%s", (rid,))
            (ask,) = cur.fetchone()
        assert ask == "[__test__] main() round-trip"
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_asks WHERE id=%s", (rid,))
            c.commit()
