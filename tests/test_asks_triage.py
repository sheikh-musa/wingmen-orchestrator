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


def test_parser_accepts_triaged_by_override():
    args = at.build_parser().parse_args(["1", "not", "--triaged-by", "cc-oeh"])
    assert args.triaged_by == "cc-oeh"


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


# ── identity resolution (bus #47221): fail-closed, never blindly 'orch-console' ─
def test_triage_stamps_triaged_by_from_resolved_agent_identity(operator_ledger_db):
    """set_test_env (conftest.py) exports CC_BASE_AGENT_ID=cc-test-harness for
    the whole suite — this is the normal fleet-lane case (a lane running
    asks_triage.py must be attributed as itself, not the console)."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    at.triage(rid, "not")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triaged_by FROM operator_asks WHERE id=%s", (rid,))
        (triaged_by,) = cur.fetchone()
    assert triaged_by == "cc-test-harness"


def test_triage_console_body_stamps_triaged_by_orch_console(operator_ledger_db, monkeypatch):
    import psycopg
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.setenv("ORCH_AGENT_ID", "orch-console")
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    at.triage(rid, "not")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triaged_by FROM operator_asks WHERE id=%s", (rid,))
        (triaged_by,) = cur.fetchone()
    assert triaged_by == "orch-console"


def test_triage_raises_when_no_identity_resolves(monkeypatch):
    """The old bug (bus #47221): asks_triage.py defaulted triaged_by to
    $ORCH_AGENT_ID or 'orch-console' — since every process that sources the
    orchestrator .env inherits ORCH_AGENT_ID=orch-console as fleet-wide
    noise, 19 rows triaged by cc-oeh/cc-angullia got misattributed to the
    console. Must now REFUSE rather than guess."""
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    with pytest.raises(RuntimeError):
        at.triage(1, "not", dsn="postgresql://unused")


def test_triage_explicit_triaged_by_overrides_env(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    at.triage(rid, "not", triaged_by="cc-oeh")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triaged_by FROM operator_asks WHERE id=%s", (rid,))
        (triaged_by,) = cur.fetchone()
    assert triaged_by == "cc-oeh"


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


# ── migration 085: client-channel done-gate + committed_date (bus #47114 3a/3c) ─
def _insert_client_channel(conn, ask="[__test__] client-channel row", delegated_to=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, ask_surface, delegated_to, chase_by) "
            "VALUES (%s,'client-channel',%s, now() + interval '24 hours') RETURNING id",
            (ask, delegated_to),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def test_operator_surface_done_is_never_gated_by_committed_date(operator_ledger_db):
    """Regression: ask_surface='operator' rows (the default) must keep working
    exactly as before migration 085 — plain --evidence, no date/outbound
    required (bus #47110 item 3c-e)."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(rid, "done", evidence="am#1")
    assert n == 1


def test_client_channel_done_without_evidence_or_date_raises(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
    with pytest.raises(ValueError):
        at.triage(rid, "done")


def test_client_channel_done_with_evidence_succeeds(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
    n = at.triage(rid, "done", evidence="am#47200")
    assert n == 1


def test_client_channel_done_with_committed_date_but_no_outbound_raises(operator_ledger_db):
    """A date with no matching outbound send never satisfies the gate — the
    whole point is 'was it actually SENT to the client' (bus #47114 item 3a)."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
    with pytest.raises(ValueError):
        at.triage(rid, "done", committed_date="2026-10-05T00:00:00Z")


def test_client_channel_done_with_committed_date_and_outbound_succeeds(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        rid = _insert_client_channel(c)
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered) "
            "VALUES ('outbound','telegram','gazzabyte-irsyad','[__test__] date sent',true) "
            "RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        c.commit()
    n = at.triage(rid, "done", committed_date="2026-10-05T00:00:00Z", outbound_msg_id=outbound_id)
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, committed_date, outbound_msg_id FROM operator_asks WHERE id=%s",
            (rid,),
        )
        triage_state, committed_date, ob = cur.fetchone()
    assert triage_state == "done"
    assert committed_date is not None
    assert ob == outbound_id


def test_client_channel_done_uses_committed_date_already_on_the_row(operator_ledger_db):
    """A row that already carries a valid committed_date+outbound (set by an
    earlier 'ask' triage) needs no fresh --committed-date/--outbound-msg-id on
    the 'done' call itself."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        rid = _insert_client_channel(c)
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered) "
            "VALUES ('outbound','telegram','gazzabyte-irsyad','[__test__] date sent 2',true) "
            "RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        c.commit()
    at.triage(rid, "ask", summary="in progress, dated",
              committed_date="2026-10-05T00:00:00Z", outbound_msg_id=outbound_id)
    n = at.triage(rid, "done")
    assert n == 1


def test_client_channel_ask_without_committed_date_does_not_change_chase_by(operator_ledger_db):
    """A plain 'in progress' triage (no --committed-date) must NOT reschedule
    the chase net — bus #47114 item 3a/b: only a real dated commitment moves
    chase_by."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT chase_by FROM operator_asks WHERE id=%s", (rid,))
        (before,) = cur.fetchone()
    at.triage(rid, "ask", summary="in progress, no date yet")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT chase_by, closed_at FROM operator_asks WHERE id=%s", (rid,))
        after, closed_at = cur.fetchone()
    assert after == before
    assert closed_at is None, "'ask' triage must never close a client-channel row"


# ── bus #47349: chase_by cleared on 'not', restored on re-triage to 'ask' ────
def test_triage_not_clears_chase_by_on_client_channel_row(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)  # chase_by = now() + 24h
    n = at.triage(rid, "not")
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT chase_by FROM operator_asks WHERE id=%s", (rid,))
        (chase_by,) = cur.fetchone()
    assert chase_by is None, (
        "bus #47349: a row triaged not_an_ask must never keep a live chase "
        "deadline, belt-and-suspenders alongside the chase query's own filter"
    )


def test_triage_ask_restores_chase_by_after_not_an_ask(operator_ledger_db):
    """test (c)-3: re-triaging not_an_ask -> ask must restore the chase with
    a fresh default window, not leave chase_by permanently NULL."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
    at.triage(rid, "not")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT chase_by FROM operator_asks WHERE id=%s", (rid,))
        (cleared,) = cur.fetchone()
    assert cleared is None
    at.triage(rid, "ask", summary="actually this WAS a real request")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT chase_by, triage_state, closed_at FROM operator_asks WHERE id=%s", (rid,)
        )
        chase_by, triage_state, closed_at = cur.fetchone()
    assert triage_state == "ask"
    assert closed_at is None
    assert chase_by is not None
    from datetime import datetime, timezone
    assert chase_by > datetime.now(timezone.utc), "restored chase_by must be a fresh future deadline"


def test_client_channel_ask_with_committed_date_extends_chase_by(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        rid = _insert_client_channel(c)
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, tag, text, delivered) "
            "VALUES ('outbound','telegram','gazzabyte-irsyad','[__test__] date sent 3',true) "
            "RETURNING id"
        )
        outbound_id = cur.fetchone()[0]
        c.commit()
    at.triage(rid, "ask", summary="dated commitment",
              committed_date="2026-11-01T00:00:00Z", outbound_msg_id=outbound_id)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT chase_by, committed_date, outbound_msg_id FROM operator_asks WHERE id=%s",
            (rid,),
        )
        chase_by, committed_date, ob = cur.fetchone()
    assert committed_date is not None
    assert ob == outbound_id
    assert chase_by >= committed_date
