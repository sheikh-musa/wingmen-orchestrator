"""scripts/asks_open.py — piece 3 of op#22669. Empty-ask validation is pure;
the INSERT itself is exercised against the ephemeral operator_ledger_db fixture
(tests/conftest.py) so a schema mismatch fails the suite, never production
(orch-console bus #44006/op#22741: this used to run against the live substrate
via os.environ DATABASE_URL)."""
import importlib

import pytest

ao = importlib.import_module("scripts.asks_open")


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


# ── round-trip against the ephemeral harness ─────────────────────────────────
def test_open_ask_inserts_with_waiting_on_operator_true(operator_ledger_db):
    import psycopg
    rid = ao.open_ask("[__test__] asks_open round-trip", delegated_to="__test_body__")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
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


def test_open_ask_computes_chase_by_from_hours(operator_ledger_db):
    import psycopg
    rid = ao.open_ask("[__test__] chase-hours round-trip", chase_hours=6.0,
                      delegated_to="__test_body__")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT extract(epoch FROM (chase_by - now())) FROM operator_asks WHERE id=%s", (rid,)
        )
        (delta_seconds,) = cur.fetchone()
    # allow generous slack for round-trip latency; must be close to 6h
    assert 6 * 3600 - 60 <= delta_seconds <= 6 * 3600 + 60


def test_open_ask_links_outbound_msg_id(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, text, delivered, tg_message_id) "
            "VALUES ('outbound','telegram','[__test__] linked outbound',true,313131) RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        c.commit()

    rid = ao.open_ask("[__test__] linked ask", outbound_msg_id=outbound_id,
                      delegated_to="__test_body__")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT outbound_msg_id FROM operator_asks WHERE id=%s", (rid,))
        (got,) = cur.fetchone()
    assert got == outbound_id


def test_open_ask_sets_triage_state_ask_with_summary_at_insert(operator_ledger_db):
    """migration 082: asks_open.py rows are always deliberate, already-
    summarized asks — they need no human triage step, unlike
    maybe_track_ask()'s raw inbound captures which start at 'captured'."""
    import psycopg
    rid = ao.open_ask("[__test__] triage-at-open round-trip", delegated_to="__test_body__")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, triage_summary, triaged_by FROM operator_asks WHERE id=%s",
            (rid,),
        )
        triage_state, triage_summary, triaged_by = cur.fetchone()
    assert triage_state == "ask"
    assert triage_summary == "[__test__] triage-at-open round-trip"
    assert triaged_by == "asks_open"


# ── identity resolution (bus #47221): fail-closed, never blindly 'orch-console' ─
def test_open_ask_defaults_delegated_to_resolved_agent_identity(operator_ledger_db):
    """set_test_env (conftest.py) exports CC_BASE_AGENT_ID=cc-test-harness for
    the whole suite — this is the normal fleet-lane case (CC_BASE_AGENT_ID set
    by launch_dangerous_cc.sh)."""
    import psycopg
    rid = ao.open_ask("[__test__] default delegated_to")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT delegated_to FROM operator_asks WHERE id=%s", (rid,))
        (delegated_to,) = cur.fetchone()
    assert delegated_to == "cc-test-harness"


def test_open_ask_console_body_defaults_delegated_to_orch_console(operator_ledger_db, monkeypatch):
    """The ONE case where trusting ORCH_AGENT_ID is correct: the console body
    itself (ORCH_BODY_ROLE=console) sending its own --ask via nazim_send.sh."""
    import psycopg
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.setenv("ORCH_AGENT_ID", "orch-console")
    rid = ao.open_ask("[__test__] console default delegated_to")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT delegated_to FROM operator_asks WHERE id=%s", (rid,))
        (delegated_to,) = cur.fetchone()
    assert delegated_to == "orch-console"


def test_open_ask_raises_when_no_identity_resolves(monkeypatch):
    """The old bug (bus #47221): ORCH_AGENT_ID is fleet-wide .env noise every
    process inherits — blindly trusting it (or defaulting to 'orch-console')
    silently misattributed asks opened by other bodies. Now it must REFUSE
    rather than guess."""
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    with pytest.raises(RuntimeError):
        ao.open_ask("a real ask", dsn="postgresql://unused")


# ── main(): exit codes ────────────────────────────────────────────────────────
def test_main_returns_2_and_prints_error_on_empty_ask(capsys):
    rc = ao.main([""])
    captured = capsys.readouterr()
    assert rc == 2
    assert "error:" in captured.err


def test_main_prints_id_and_returns_0_on_success(operator_ledger_db, capsys):
    import psycopg
    rc = ao.main(["[__test__] main() round-trip", "--delegated-to", "__test_body__"])
    captured = capsys.readouterr()
    assert rc == 0
    rid = int(captured.out.strip())
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT ask FROM operator_asks WHERE id=%s", (rid,))
        (ask,) = cur.fetchone()
    assert ask == "[__test__] main() round-trip"
