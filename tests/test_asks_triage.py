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


def test_triage_done_keeps_summary(operator_ledger_db):
    """console #81245: a 'done' close passed --summary must store triage_summary
    (it was silently dropped, leaving a 'summary pending' row on the Builds page)."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(rid, "done", summary="Answered in chat", evidence="am#1")
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_summary FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Answered in chat"


def test_triage_done_without_summary_keeps_existing_summary(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    at.triage(rid, "ask", summary="Original line")
    at.triage(rid, "done", evidence="am#2")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_summary FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Original line"


def test_triage_not_keeps_summary(operator_ledger_db):
    """console #81296: a 'not' close passed --summary must store triage_summary."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(rid, "not", summary="Acknowledgement, no request")
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_state, triage_summary FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone() == ("not_an_ask", "Acknowledgement, no request")


def test_triage_not_without_summary_keeps_existing_summary(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    at.triage(rid, "ask", summary="Original line")
    at.triage(rid, "not")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_summary FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Original line"


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


# ── migration 132 (console bus #80707/#80718/#80719): --outbound-msg-id must
# be a REAL outbound operator_messages row that actually postdates the ask
# and (warn, not refuse) actually states the committed date ────────────────
def _insert_outbound_msg(conn, *, text="[__test__] outbound", created_at="now()"):
    """`created_at` is a raw SQL expression (e.g. "now() - interval '1 day'"),
    interpolated directly like _insert_ask/_insert_client_channel's own
    created_at handling — never a bind parameter."""
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO operator_messages (direction, channel, tag, text, delivered, created_at) "
            f"VALUES ('outbound','telegram','gazzabyte-irsyad',%s,true,{created_at}) RETURNING id",
            (text,),
        )
        mid = cur.fetchone()[0]
    conn.commit()
    return mid


def test_outbound_msg_id_rejects_a_nonexistent_row(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
    with pytest.raises(ValueError):
        at.triage(rid, "ask", summary="dated", committed_date="2026-11-06T00:00:00Z",
                  outbound_msg_id=999999999)


def test_outbound_msg_id_rejects_an_inbound_row(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        rid = _insert_client_channel(c)
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, text, delivered) "
            "VALUES ('inbound','telegram','[__test__] musa asked something',true) RETURNING id"
        )
        inbound_id = cur.fetchone()[0]
        c.commit()
    with pytest.raises(ValueError):
        at.triage(rid, "ask", summary="dated", committed_date="2026-11-06T00:00:00Z",
                  outbound_msg_id=inbound_id)


def test_outbound_msg_id_rejects_a_message_sent_before_the_ask_existed(operator_ledger_db):
    """A message from before the ask existed cannot have committed its date
    (console bus #80718)."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        outbound_id = _insert_outbound_msg(c, created_at="now() - interval '1 day'")
        rid = _insert_client_channel(c)
    with pytest.raises(ValueError):
        at.triage(rid, "ask", summary="dated", committed_date="2026-11-06T00:00:00Z",
                  outbound_msg_id=outbound_id)


def test_outbound_msg_id_accepts_a_message_sent_after_the_ask_was_created(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
        outbound_id = _insert_outbound_msg(c, text="Confirmed: 2026-11-06.")
    n = at.triage(rid, "ask", summary="dated", committed_date="2026-11-06T00:00:00Z",
                  outbound_msg_id=outbound_id)
    assert n == 1


def test_outbound_msg_id_warns_but_does_not_refuse_when_date_not_in_text(operator_ledger_db, capsys):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
        outbound_id = _insert_outbound_msg(c, text="[__test__] no date mentioned here at all")
    n = at.triage(rid, "ask", summary="dated", committed_date="2026-11-06T00:00:00Z",
                  outbound_msg_id=outbound_id)
    assert n == 1, "must warn, never refuse, when the date doesn't visibly appear"
    captured = capsys.readouterr()
    assert "does not appear" in captured.err


@pytest.mark.parametrize("form", ["iso", "mmdd", "day_mon", "mon_day", "weekday_day_mon"])
def test_outbound_msg_id_no_warning_when_date_appears_in_a_recognized_form(operator_ledger_db, capsys, form):
    import datetime as _dt
    import psycopg
    d = _dt.date(2026, 11, 6)
    text_by_form = {
        "iso": f"Confirmed: {d.strftime('%Y-%m-%d')}.",
        "mmdd": f"Confirmed: {d.strftime('%m-%d')}.",
        "day_mon": f"Confirmed: {d.day} {d.strftime('%b')}.",
        "mon_day": f"Confirmed: {d.strftime('%b')} {d.day}.",
        "weekday_day_mon": f"Confirmed: {d.strftime('%a')} {d.day} {d.strftime('%b')}.",
    }
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_client_channel(c)
        outbound_id = _insert_outbound_msg(c, text=text_by_form[form])
    at.triage(rid, "ask", summary="dated", committed_date="2026-11-06T00:00:00Z",
              outbound_msg_id=outbound_id)
    captured = capsys.readouterr()
    assert "does not appear" not in captured.err


def test_console_80719_correction_6_nov_2026_is_a_friday():
    """Pure sanity anchor for console's own correction (bus #80719): 6 Nov
    2026 is a FRIDAY, not a Thursday as console's #80718 example first said.
    Documents why the weekday form in _date_appears_in_text must always be
    computed from the date (strftime), never hard-coded — a wrong hard-coded
    weekday would have baked that exact slip into this suite."""
    import datetime as _dt
    assert _dt.date(2026, 11, 6).strftime("%a") == "Fri"


# --- migration 118: needs-Musa flag + plain-language operator_summary --------
# (orch-console bus #75387: the seed path, and the go-forward maintenance path —
# asks_triage.py sets/unsets the flag and writes the Musa-readable line.)

def test_parser_accepts_needs_musa_flags():
    args = at.build_parser().parse_args(
        ["12", "ask", "--summary", "one line", "--needs-musa", "on",
         "--musa-summary", "Decide the events-dashboard scope."]
    )
    assert args.needs_musa == "on"
    assert args.musa_summary == "Decide the events-dashboard scope."


def test_parser_needs_musa_defaults_to_untouched():
    args = at.build_parser().parse_args(["12", "ask", "--summary", "one line"])
    assert args.needs_musa is None
    assert args.musa_summary is None


def test_triage_ask_sets_needs_musa_flag_and_summary(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    n = at.triage(
        rid, "ask", summary="internal triage line", dsn=operator_ledger_db,
        needs_musa="on", musa_summary="Decide the zone-sharing model.",
    )
    assert n == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT needs_operator_decision, operator_summary "
            "FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone() == (True, "Decide the zone-sharing model.")


def test_triage_ask_without_needs_musa_leaves_flag_untouched(operator_ledger_db):
    # tri-state: absent flag = don't touch, so a plain re-triage can never
    # silently clear a Musa decision-pending mark (same COALESCE discipline
    # as delegated_to).
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
        with c.cursor() as cur:
            cur.execute(
                "UPDATE operator_asks SET needs_operator_decision=true "
                "WHERE id=%s", (rid,))
        c.commit()
    at.triage(rid, "ask", summary="internal triage line", dsn=operator_ledger_db)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT needs_operator_decision FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone() == (True,)


def test_triage_ask_needs_musa_off_clears_flag(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
        with c.cursor() as cur:
            cur.execute(
                "UPDATE operator_asks SET needs_operator_decision=true "
                "WHERE id=%s", (rid,))
        c.commit()
    at.triage(rid, "ask", summary="internal triage line", dsn=operator_ledger_db,
              needs_musa="off")
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT needs_operator_decision FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone() == (False,)


# ── --delivered: LIVE-verified stamp, first stamp wins (op#34803, orch-console #78198) ──
def test_parser_accepts_delivered_flag():
    args = at.build_parser().parse_args(["5", "ask", "--summary", "s", "--delivered"])
    assert args.delivered is True


def test_stamp_delivered_is_first_stamp_wins(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    assert at.stamp_delivered(rid) == 1
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT delivered_at FROM operator_asks WHERE id=%s", (rid,))
        first = cur.fetchone()[0]
    assert first is not None
    assert at.stamp_delivered(rid) == 0          # repeat: no-op, not an error
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT delivered_at FROM operator_asks WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == first


def test_delivered_rides_along_with_ask_and_keeps_the_row_open(operator_ledger_db):
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    assert at.main([str(rid), "ask", "--summary", "LIVE, awaiting Musa confirm", "--delivered"]) == 0
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_state, closed_at, delivered_at FROM operator_asks WHERE id=%s", (rid,))
        state, closed_at, delivered_at = cur.fetchone()
    assert state == "ask" and closed_at is None and delivered_at is not None
# ── --answers (orch-console bus #78269, op#34850): root-cause fix for stale  ──
# pending asks. Musa usually ANSWERS with a fresh message, not a Telegram
# reply, so the reply-link auto-close misses it; when triaging the captured
# row that IS the answer, --answers N confirms+closes ask N and links the
# answering operator message (this row's source_msg_id) as evidence.
def test_parser_accepts_answers_flag():
    args = at.build_parser().parse_args(["31", "not", "--answers", "77"])
    assert args.id == 31
    assert args.action == "not"
    assert args.answers == 77


def test_answers_closes_target_ask_with_confirm_semantics(operator_ledger_db):
    """The target ask gets the SAME close the operator's swipe-to-confirm
    writes (asks_close.py confirm): confirmed_at + closed_at +
    closed_reason='operator_done', plus evidence naming the answering op msg."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        target = _insert_captured(c, "[__test__] the ask being answered")
        cur = c.cursor()
        cur.execute(
            "UPDATE operator_asks SET triage_state='ask', triage_summary='s' "
            "WHERE id=%s", (target,))
        ans = _insert_captured(c, "[__test__] yes go ahead with option B")
        cur.execute(
            "UPDATE operator_asks SET source_msg_id=4242 WHERE id=%s", (ans,))
        c.commit()
    n = at.triage(ans, "not", answers=target, dsn=operator_ledger_db)
    assert n == 1  # main row still triaged normally
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "SELECT triage_state, closed_at, closed_reason FROM operator_asks "
            "WHERE id=%s ORDER BY id", (target,))
        state, closed_at, reason = cur.fetchone()
        assert state == "ask"  # the ANSWER row is 'not'; the target keeps its triage
        assert closed_at is not None
        assert reason == "operator_done"
        cur.execute(
            "SELECT confirmed_at, triage_evidence_ref FROM operator_asks WHERE id=%s",
            (target,))
        confirmed_at, evidence = cur.fetchone()
        assert confirmed_at is not None
        assert "4242" in (evidence or ""), "evidence links the answering op msg"


def test_answers_unknown_ask_id_raises(operator_ledger_db):
    """A typo'd ASK_ID must fail loudly, never silently pass as answered."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        ans = _insert_captured(c)
    with pytest.raises(ValueError):
        at.triage(ans, "not", answers=987654, dsn=operator_ledger_db)


def test_answers_refuses_to_name_the_row_being_triaged(operator_ledger_db):
    """--answers <own id> is a semantic tangle (triage + confirm on one row);
    refuse it rather than pick a winner silently."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_captured(c)
    with pytest.raises(ValueError):
        at.triage(rid, "not", answers=rid, dsn=operator_ledger_db)


def test_answers_on_already_closed_target_is_idempotent_ok(operator_ledger_db):
    """Already-closed target: the goal state (closed) already holds — no
    raise, no reopen, evidence left as it was."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        target = _insert_captured(c)
        cur = c.cursor()
        cur.execute(
            "UPDATE operator_asks SET triage_state='done', closed_at=now(), "
            "closed_reason='done' WHERE id=%s", (target,))
        c.commit()
        ans = _insert_captured(c)
    at.triage(ans, "not", answers=target, dsn=operator_ledger_db)  # must not raise
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_reason FROM operator_asks WHERE id=%s", (target,))
        assert cur.fetchone() == ("done",)


def test_answers_cli_on_already_closed_target_exits_zero(operator_ledger_db, capsys):
    """cc-quality 78363/78368 finding 2 (wet-proven): the ANSWER row is still
    triage-eligible but the TARGET ask is already closed — the idempotent
    re-run shape a sweeper hits. The CLI must report ok/exit 0 (the goal
    state holds), never the old exit-2 "0 rows updated", which reads as a
    hard failure on a correct DB."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        target = _insert_captured(c, "[__test__] the ask already closed")
        cur = c.cursor()
        cur.execute(
            "UPDATE operator_asks SET triage_state='done', closed_at=now(), "
            "closed_reason='done' WHERE id=%s", (target,))
        ans = _insert_captured(c, "[__test__] the answering message")
        c.commit()
    rc = at.main([str(ans), "not", "--answers", str(target)])
    out = capsys.readouterr().out
    assert rc == 0, f"already-closed target is idempotent ok, not a failure (out={out!r})"
    assert out.startswith("ok")


def test_answers_primary_miss_exits_2_and_leaves_target_open(operator_ledger_db, capsys):
    """cc-quality 78363/78368 finding 2, second face: when the PRIMARY triage
    update touches nothing (typo'd item id), the CLI must fail — and the
    --answers close must NOT have fired either: a triage that didn't happen
    cannot be the evidence that an ask was answered."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c:
        target = _insert_captured(c, "[__test__] open target ask")
        c.commit()
    rc = at.main([str(987654), "not", "--answers", str(target)])
    assert rc == 2
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_at FROM operator_asks WHERE id=%s", (target,))
        assert cur.fetchone()[0] is None, \
            "target must stay open when the primary triage no-ops"


# ── op#34803 (a): delivered_at must be the LIVE time, not the bookkeeping time ──
# asks_triage --delivered used to stamp now() unconditionally, so every catch-up
# close recorded when someone did the bookkeeping (0/49 manual stamps in a 24h
# sample sat within 30 min of their own evidence row, orch-console #79947).
import datetime as _dt

import psycopg


def _mk_agent_messages(conn):
    # the ledger fixture doesn't carry agent_messages (other suites create their own)
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS agent_messages ("
                    " id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,"
                    " subject text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now())")
    conn.commit()


def _bus_row(conn, age_min):
    _mk_agent_messages(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_messages (subject, created_at) VALUES "
                    "('[__test__] delivered', now() - make_interval(mins => %s)) RETURNING id, created_at",
                    (age_min,))
        r = cur.fetchone()
    conn.commit()
    return r


def _op_row(conn, age_min):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO operator_messages (direction, text, created_at) VALUES "
                    "('outbound', '[__test__] answer', now() - make_interval(mins => %s)) "
                    "RETURNING id, created_at", (age_min,))
        r = cur.fetchone()
    conn.commit()
    return r


def _aged_ask(conn, age_min=600):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO operator_asks (ask, created_at) VALUES "
                    "('[__test__] aged ask', now() - make_interval(mins => %s)) RETURNING id", (age_min,))
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def _delivery(dsn, rid):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT delivered_at, delivered_source, delivered_evidence_ref, closed_at "
                    "FROM operator_asks WHERE id=%s", (rid,))
        return cur.fetchone()


def test_parse_evidence_refs_finds_bus_and_op_ids_only():
    refs = at.parse_evidence_refs("onboarding bus#78336 + bus #78798; answered op#35030 (#79792)")
    assert refs == [("bus", 78336), ("bus", 78798), ("op", 35030)]   # bare '#N' is ambiguous: ignored
    assert at.parse_evidence_refs(None) == [] and at.parse_evidence_refs("PR#555 merged") == []


def test_delivered_at_bus_ref_stamps_that_rows_time(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, bus_ts = _bus_row(c, age_min=240)
    rc = at.main([str(rid), "done", "--evidence", f"bus#{bus_id}", "--delivered-at", f"bus#{bus_id}",
                  "--musa-summary", "s"])
    assert rc == 0
    d_at, src, ref, closed = _delivery(operator_ledger_db, rid)
    # 'manual', never 'bus-report': that source is backfill-only (ruling #78610) and the live
    # operator_asks_bus_evidence_backfill_only_chk rejects it (caught applying ask 4225, 10-28)
    assert d_at == bus_ts and src == "manual" and ref == f"bus#{bus_id}" and closed is not None


def test_delivered_at_op_ref_stamps_the_operator_message_time(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        op_id, op_ts = _op_row(c, age_min=90)
    assert at.main([str(rid), "done", "--delivered-at", f"op#{op_id}", "--musa-summary", "s"]) == 0
    d_at, src, ref, _ = _delivery(operator_ledger_db, rid)
    assert d_at == op_ts and src == "manual" and ref == f"op#{op_id}"


def test_delivered_at_iso_stamps_exactly_that_time(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
    when = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=3)).replace(microsecond=0)
    assert at.main([str(rid), "done", "--delivered-at", when.isoformat().replace("+00:00", "Z"),
                    "--musa-summary", "s"]) == 0
    d_at, src, ref, _ = _delivery(operator_ledger_db, rid)
    assert d_at == when and src == "manual" and ref.startswith("iso:")


@pytest.mark.parametrize("value", ["bus#999999", "op#999999", "yesterday-ish", "2026-10-28T10:00:00"])
def test_delivered_at_unresolvable_or_naive_refuses_and_writes_nothing(operator_ledger_db, value):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        _mk_agent_messages(c)
    assert at.main([str(rid), "done", "--delivered-at", value]) == 2
    d_at, _, _, closed = _delivery(operator_ledger_db, rid)
    assert d_at is None and closed is None          # refused BEFORE the triage write, not half-applied


def test_delivered_at_in_the_future_or_before_the_ask_refuses(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c, age_min=60)
    future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).isoformat()
    before_ask = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=5)).isoformat()
    assert at.main([str(rid), "done", "--delivered-at", future]) == 2
    assert at.main([str(rid), "done", "--delivered-at", before_ask]) == 2
    assert _delivery(operator_ledger_db, rid)[0] is None


def test_bare_delivered_with_old_evidence_refuses_with_hint(operator_ledger_db, capsys):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, _ = _bus_row(c, age_min=120)
    assert at.main([str(rid), "done", "--evidence", f"bus#{bus_id}", "--delivered",
                    "--musa-summary", "s"]) == 2
    err = capsys.readouterr().err
    assert f"--delivered-at bus#{bus_id}" in err
    d_at, _, _, closed = _delivery(operator_ledger_db, rid)
    assert d_at is None and closed is None


def test_bare_delivered_checks_evidence_already_on_the_row(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, _ = _bus_row(c, age_min=120)
        with c.cursor() as cur:
            cur.execute("UPDATE operator_asks SET triage_evidence_ref=%s WHERE id=%s", (f"bus#{bus_id}", rid))
        c.commit()
    assert at.main([str(rid), "ask", "--summary", "s", "--delivered"]) == 2
    assert _delivery(operator_ledger_db, rid)[0] is None


def test_bare_delivered_with_fresh_evidence_still_stamps_now(operator_ledger_db):
    # the case --delivered was built for: you verified it live just now
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, _ = _bus_row(c, age_min=5)
    assert at.main([str(rid), "done", "--evidence", f"bus#{bus_id}", "--delivered",
                    "--musa-summary", "s"]) == 0
    d_at = _delivery(operator_ledger_db, rid)[0]
    assert abs((_dt.datetime.now(_dt.timezone.utc) - d_at).total_seconds()) < 60


def test_bare_delivered_without_any_ref_still_stamps_now(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
    assert at.main([str(rid), "done", "--evidence", "verified live in the console just now", "--delivered",
                    "--musa-summary", "s"]) == 0
    assert _delivery(operator_ledger_db, rid)[0] is not None


# ── no-summary refuses a delivery stamp (console op#35502/35503 follow-up,   ──
# orch-console bus #81244, root-caused live on operator_asks #5690: `done
# --evidence ... --delivered-at op#...` with no summary left /builds showing
# the internal pending-placeholder next to a real elapsed time) ──
def test_delivered_refuses_with_no_summary_anywhere(operator_ledger_db, capsys):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, _ = _bus_row(c, age_min=5)
    rc = at.main([str(rid), "done", "--evidence", f"bus#{bus_id}", "--delivered"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "requires a written summary" in err
    assert _delivery(operator_ledger_db, rid)[:2] == (None, None)


def test_delivered_at_refuses_with_no_summary_writes_nothing(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        op_id, _ = _op_row(c, age_min=5)
    assert at.main([str(rid), "done", "--delivered-at", f"op#{op_id}"]) == 2
    d_at, _, _, closed = _delivery(operator_ledger_db, rid)
    assert d_at is None and closed is None, "refused BEFORE the triage write, not half-applied"


def test_delivered_succeeds_when_musa_summary_supplied_this_call(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, _ = _bus_row(c, age_min=5)
    rc = at.main([str(rid), "done", "--evidence", f"bus#{bus_id}", "--delivered",
                  "--musa-summary", "Added the thing Musa asked for"])
    assert rc == 0
    assert _delivery(operator_ledger_db, rid)[0] is not None


def test_delivered_succeeds_when_triage_summary_already_on_the_row(operator_ledger_db):
    """A prior `ask --summary ...` triage already wrote triage_summary; a later
    `done --delivered` (no new summary) must not be refused -- the summary
    exists, it just wasn't supplied THIS call."""
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, _ = _bus_row(c, age_min=5)
    assert at.main([str(rid), "ask", "--summary", "already triaged earlier"]) == 0
    assert at.main([str(rid), "done", "--evidence", f"bus#{bus_id}", "--delivered"]) == 0
    assert _delivery(operator_ledger_db, rid)[0] is not None


def test_delivered_rides_with_ask_action_summary_this_call_satisfies_the_gate(operator_ledger_db):
    """--summary only applies to action=ask, but it DOES count as 'a summary
    was supplied this call' for the delivery gate -- covered already by
    test_delivered_rides_along_with_ask_and_keeps_the_row_open, this adds the
    explicit no-prior-row-summary case."""
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
    assert at.main([str(rid), "ask", "--summary", "LIVE now", "--delivered"]) == 0
    assert _delivery(operator_ledger_db, rid)[0] is not None


def test_delivered_at_first_stamp_still_wins(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        b1, ts1 = _bus_row(c, age_min=200)
        b2, _ = _bus_row(c, age_min=100)
    assert at.main([str(rid), "ask", "--summary", "s", "--delivered-at", f"bus#{b1}"]) == 0
    assert at.main([str(rid), "ask", "--summary", "s", "--delivered-at", f"bus#{b2}"]) == 0
    assert _delivery(operator_ledger_db, rid)[0] == ts1


def test_a_refused_stamp_rolls_back_the_whole_triage(operator_ledger_db):
    # orch-console #79986: triage() used to autocommit BEFORE the separate stamp, so a stamp the
    # live checks reject left a half-applied close. Now they share one transaction.
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
    bad = {"at": _dt.datetime.now(_dt.timezone.utc), "ref": "bus#1", "source": "bus-report"}
    with pytest.raises(psycopg.errors.CheckViolation):
        at.triage(rid, "done", evidence="bus#1", deliver=bad)    # bus-report is backfill-only
    d_at, _, _, closed = _delivery(operator_ledger_db, rid)
    assert d_at is None and closed is None


def test_triage_and_stamp_commit_together_and_report_stamped(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _aged_ask(c)
        bus_id, bus_ts = _bus_row(c, age_min=50)
    d = {"at": bus_ts, "ref": f"bus#{bus_id}", "source": "manual"}
    assert at.triage(rid, "done", evidence=f"bus#{bus_id}", deliver=d) == 1 and d["stamped"] == 1
    assert _delivery(operator_ledger_db, rid)[:2] == (bus_ts, "manual")
