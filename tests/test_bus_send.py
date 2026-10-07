"""test_bus_send.py — the ONE sanctioned bus-post CLI enforces priority (bus #43651).

orch-console's ask after op#22517's aftermath (a hand-written INSERT that
omitted `priority` silently landed at the table default 'P2', below the hub's
wake floor — reference_hub_wake_floor_p1_rr): make it structural, never a
promise. These are PURE-LOGIC / argparse tests — no live DB, no psycopg
connection (matches the style of test_operator_log_scope.py).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import bus_send as bs  # noqa: E402


# ---- --priority is REQUIRED, no default ------------------------------------

def test_priority_is_required():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "update",
            "--subject", "s", "--req",
        ])


def test_priority_rejects_unknown_value():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "update",
            "--subject", "s", "--priority", "URGENT",
        ])


@pytest.mark.parametrize("p", ["P0", "P1", "P2", "P3"])
def test_priority_accepts_each_valid_value(p):
    args = bs.build_parser().parse_args([
        "--to", "cc-orchestrator", "--type", "update",
        "--subject", "s", "--priority", p,
    ])
    assert args.priority == p


def test_message_type_rejects_unknown_value():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "nonsense",
            "--subject", "s", "--priority", "P1",
        ])


# ---- identity resolution: fail closed, never guess --------------------------

def test_identity_prefers_cc_base_agent_id():
    env = {"CC_BASE_AGENT_ID": "cc-substrate", "AGENT_ID": "should-not-win",
           "ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert bs.resolve_from_agent(env) == "cc-substrate"


def test_identity_falls_back_to_agent_id():
    env = {"AGENT_ID": "cc-fleet-health"}
    assert bs.resolve_from_agent(env) == "cc-fleet-health"


def test_identity_uses_orch_agent_id_only_when_console_role():
    env = {"ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert bs.resolve_from_agent(env) == "orch-console"


def test_identity_ignores_orch_agent_id_when_role_is_not_console():
    # ORCH_AGENT_ID is fleet-wide .env noise (every launcher sources .env) —
    # must never be trusted outside the one documented console-fallback case.
    env = {"ORCH_AGENT_ID": "orch-console"}
    with pytest.raises(bs.IdentityError):
        bs.resolve_from_agent(env)


def test_identity_refuses_rather_than_guesses_when_nothing_resolves():
    with pytest.raises(bs.IdentityError):
        bs.resolve_from_agent({})


# ---- empty-body guard (Nazim shipped 3 blank rows on 2026-09-05) -----------

def test_read_body_refuses_short_body():
    import io
    with pytest.raises(SystemExit):
        bs.read_body(io.StringIO("too short"))


def test_read_body_accepts_body_at_or_above_floor():
    import io
    body = "x" * bs._MIN_BODY_BYTES
    assert bs.read_body(io.StringIO(body)) == body


# ---- hub wake-floor warning (bus #44527) — warn, never refuse ---------------
# #44508 landed P1 + requires_response=FALSE and sat unseen for ~14h below the
# hub's wake floor (P0/P1 AND requires_response). commitment_sweeper.py fixes
# the requires_response side; this is the bus_send.py side — a caller who
# posts --to cc-orchestrator --req below P1 gets a stderr warning (not a
# refusal: P2-rr is a legitimate way to post a non-urgent item).

def test_warns_when_hub_bound_req_below_p1(capsys):
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", True, "P2")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "will NOT wake the hub" in err
    assert "P2" in err


def test_no_warning_when_hub_bound_req_at_p1():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", True, "P1", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_when_hub_bound_req_at_p0():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", True, "P0", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_when_not_requires_response():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-orchestrator", False, "P2", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_when_not_addressed_to_hub():
    import io
    stream = io.StringIO()
    bs.warn_if_below_hub_wake_floor("cc-irsyad", True, "P2", stream=stream)
    assert stream.getvalue() == ""


# op#25626 / bus #51657/#51717: a P0/P1 message raising a data-security
# concern must cite a data_truth.py classification — WARNING (not a
# refusal), same shape as the hub-wake-floor check above.

def test_warns_on_data_security_claim_without_citation(capsys):
    bs.warn_if_missing_provenance_citation("P1", "possible real data exposure", "found PII in the demo DB")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "classification" in err


def test_no_warning_when_citation_present():
    import io
    stream = io.StringIO()
    bs.warn_if_missing_provenance_citation(
        "P1", "real data exposure?", "checked via data_truth.py — classification: SYNTHETIC", stream=stream,
    )
    assert stream.getvalue() == ""


def test_no_warning_when_no_data_security_keyword():
    import io
    stream = io.StringIO()
    bs.warn_if_missing_provenance_citation("P1", "deploy status", "build is green", stream=stream)
    assert stream.getvalue() == ""


def test_no_warning_below_p1():
    import io
    stream = io.StringIO()
    bs.warn_if_missing_provenance_citation("P2", "real data exposure", "found PII", stream=stream)
    assert stream.getvalue() == ""


def test_dry_run_still_warns_below_hub_wake_floor(monkeypatch, capsys):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P2", "--req", "--dry-run",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "will NOT wake the hub" in err


# ---- end-to-end CLI, --dry-run so it never touches the DB -------------------

def test_dry_run_end_to_end(monkeypatch, capsys):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--req", "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "priority=P1" in out
    assert "from=cc-substrate" in out


def test_dry_run_still_enforces_empty_body_guard(monkeypatch):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("too short"))
    with pytest.raises(SystemExit):
        bs.main([
            "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
            "--priority", "P1", "--dry-run",
        ])


def test_main_refuses_when_identity_cannot_resolve(monkeypatch, capsys):
    import io
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--dry-run",
    ])
    assert rc == 2
    assert "cannot resolve" in capsys.readouterr().err


# ---- send() — the one INSERT, reused by _bus_tmp.py / scratchpad shims -----
# (bus #43673). priority validation happens before any DB connection, so
# these are safe to test directly with no live DB.

def test_send_rejects_invalid_priority():
    with pytest.raises(ValueError):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, "URGENT")


def test_send_rejects_missing_priority():
    with pytest.raises(ValueError):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, None)


@pytest.mark.parametrize("p", ["P0", "P1", "P2", "P3"])
def test_send_accepts_valid_priority_then_reaches_the_db_call(monkeypatch, p):
    # Confirms priority validation doesn't reject a valid value — push past
    # it into psycopg2.connect (monkeypatched to fail fast) so no real DB
    # connection is attempted. dsn is passed explicitly so this doesn't
    # depend on DATABASE_URL/.env being present (CI has neither).
    import psycopg2

    def _boom(*a, **k):
        raise RuntimeError("no live DB in tests")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(RuntimeError, match="no live DB in tests"):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, p, dsn="postgresql://unused")


# ---- --link-ask (Musa op#23554, bus #46353): a delegation is NEVER a new
# ask — send() only ever LINKS an existing operator_asks row via --link-ask.
# The old is_new_ask auto-create heuristic (op#22669) is REVERTED: it couldn't
# tell a genuine Musa ask from a pure fleet delegation and phantom-appeared on
# his "Your asks" board (ids 378/381). Fake psycopg2 connection/cursor
# (mirrors tests/console/test_app.py's _FakeCursor/_FakeConn) so the REAL SQL
# send() builds is exercised, not a stand-in.

class _FakeCursor:
    def __init__(self, fetch_queue, rowcount=1):
        self.executed = []
        self._fetch = list(fetch_queue)
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetch.pop(0)


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False
        self.closed = False
        self.rolled_back = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _fake_send(monkeypatch, fetch_queue, rowcount=1):
    import psycopg2
    cur = _FakeCursor(fetch_queue, rowcount=rowcount)
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)
    return cur, conn


def test_send_never_creates_a_new_operator_asks_row(monkeypatch):
    # A fresh orch-console decision with --req and no --link-ask must NOT
    # touch operator_asks at all — that's exactly the ids-378/381 phantom-ask
    # bug (Musa op#23554, bus #46353). set_config (no fetchone), then the
    # agent_messages INSERT ... RETURNING id, thread_id.
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4242, "th-uuid-abc")])
    row_id, thread_id = bs.send(
        "orch-console", "cc-irsyad", "decision", "ship the thing",
        "x" * bs._MIN_BODY_BYTES, "P1", req=True, dsn="postgresql://unused",
    )
    assert (row_id, thread_id) == (4242, "th-uuid-abc")
    assert conn.committed is True
    assert not any("operator_asks" in e[0].lower() for e in cur.executed)


def test_send_with_link_ask_updates_the_existing_row(monkeypatch):
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4242, "th-uuid-abc")], rowcount=1)
    bs.send(
        "orch-console", "cc-irsyad", "decision", "ship the thing",
        "x" * bs._MIN_BODY_BYTES, "P1", req=True, link_ask=17,
        dsn="postgresql://unused",
    )
    assert conn.committed is True
    link = next(e for e in cur.executed if "update operator_asks" in e[0].lower())
    assert link[1] == ("th-uuid-abc", "cc-irsyad", 17)
    assert "closed_at is null" in link[0].lower()
    assert not any("insert into operator_asks" in e[0].lower() for e in cur.executed)


def test_send_with_link_ask_raises_on_bad_or_closed_id(monkeypatch):
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4242, "th-uuid-abc")], rowcount=0)
    with pytest.raises(SystemExit, match="does not exist or is already closed"):
        bs.send(
            "orch-console", "cc-irsyad", "decision", "ship the thing",
            "x" * bs._MIN_BODY_BYTES, "P1", req=True, link_ask=999,
            dsn="postgresql://unused",
        )
    assert conn.committed is False
    assert conn.closed is True


# ---- BASE-vs-INSTANCE refusal (bus #49220): orch-console lost 5 rows on
# 2026-10-01 by addressing the BASE id ('cc-irsyad') while only INSTANCE
# addresses ('cc-irsyad-1') were being polled. REFUSE, not warn, unless the
# caller explicitly passes --to-base.

class _FakeInstanceCursor:
    def __init__(self, rows):
        self.executed = []
        self._rows = rows

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows


def test_live_instance_ids_queries_base_agent_id_excluding_self(monkeypatch):
    import psycopg2

    cur = _FakeInstanceCursor([("cc-irsyad-1",)])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    result = bs.live_instance_ids("cc-irsyad", dsn="postgresql://unused")

    assert result == ["cc-irsyad-1"]
    sql, params = cur.executed[0]
    assert "base_agent_id=%s" in sql
    assert "agent_id<>%s" in sql
    assert "status IN ('idle','working')" in sql
    assert params == ("cc-irsyad", "cc-irsyad")


def test_live_instance_ids_empty_for_singleton_with_no_fanout(monkeypatch):
    import psycopg2

    cur = _FakeInstanceCursor([])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    assert bs.live_instance_ids("cai", dsn="postgresql://unused") == []


def test_refuse_if_base_has_live_instances_raises_with_suggestion(monkeypatch):
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: ["cc-irsyad-1", "cc-irsyad-2"])
    monkeypatch.setattr(bs, "_heartbeat_age_str", lambda agent_id, dsn=None: "status=idle, heartbeat 0:01:00 ago")
    with pytest.raises(SystemExit, match="Did you mean --to cc-irsyad-1"):
        bs.refuse_if_base_has_live_instances("cc-irsyad", False)


def test_refuse_if_base_has_live_instances_includes_heartbeat_age_in_message(monkeypatch):
    # bus #49675: a suggested instance can be dead by the time it's acted on --
    # the refusal message must carry the DB-measured staleness so a future
    # "it suggested a dead instance" report is self-diagnosing.
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: ["cc-irsyad-1"])
    monkeypatch.setattr(bs, "_heartbeat_age_str", lambda agent_id, dsn=None: "status=offline, heartbeat 17:04:00 ago")
    with pytest.raises(SystemExit, match=r"\[status=offline, heartbeat 17:04:00 ago\]"):
        bs.refuse_if_base_has_live_instances("cc-irsyad", False)


def test_heartbeat_age_str_reports_status_and_age(monkeypatch):
    import datetime
    import psycopg2

    cur = _FakeCursor([("offline", datetime.timedelta(hours=17, minutes=4))])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    result = bs._heartbeat_age_str("cc-irsyad-1", dsn="postgresql://unused")

    assert result == "status=offline, heartbeat 17:04:00 ago"


def test_heartbeat_age_str_handles_missing_row(monkeypatch):
    import psycopg2

    cur = _FakeCursor([None])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    assert bs._heartbeat_age_str("cc-ghost-1", dsn="postgresql://unused") == "no agent_status row"


def test_heartbeat_age_str_never_raises_on_db_error(monkeypatch):
    import psycopg2

    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(psycopg2, "connect", _boom)

    result = bs._heartbeat_age_str("cc-irsyad-1", dsn="postgresql://unused")
    assert "heartbeat age unavailable" in result


def test_refuse_if_base_has_live_instances_noop_when_none_live(monkeypatch):
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: [])
    bs.refuse_if_base_has_live_instances("cc-irsyad", False)  # must not raise


def test_to_base_flag_skips_the_live_instance_check_entirely(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("live_instance_ids must not be called when --to-base is set")

    monkeypatch.setattr(bs, "live_instance_ids", _boom)
    bs.refuse_if_base_has_live_instances("cc-irsyad", True)  # must not raise, must not query


# dry-run never touches the DB at all (pre-existing contract, see
# test_dry_run_end_to_end) -- the live-instance check runs AFTER the dry-run
# early-return, so it never fires in --dry-run mode. These exercise the real
# (non-dry-run) path instead, stubbing send() so no actual INSERT happens.

def test_cli_refuses_on_base_with_live_instances_before_sending(monkeypatch):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "orch-console")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: ["cc-irsyad-1"])
    monkeypatch.setattr(bs, "_heartbeat_age_str", lambda agent_id, dsn=None: "status=idle, heartbeat 0:01:00 ago")

    def _boom(*a, **k):
        raise AssertionError("send() must not be called when the refusal fires")

    monkeypatch.setattr(bs, "send", _boom)
    with pytest.raises(SystemExit, match="Did you mean --to cc-irsyad-1"):
        bs.main([
            "--to", "cc-irsyad", "--type", "decision", "--subject", "s",
            "--priority", "P1",
        ])


def test_cli_to_base_bypasses_refusal_and_reaches_send(monkeypatch):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "orch-console")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: ["cc-irsyad-1"])
    monkeypatch.setattr(bs, "send", lambda *a, **k: (4242, "th-uuid"))

    rc = bs.main([
        "--to", "cc-irsyad", "--type", "decision", "--subject", "s",
        "--priority", "P1", "--to-base",
    ])
    assert rc == 0


def test_send_without_link_ask_never_touches_operator_asks_on_a_reply(monkeypatch):
    # a reply_to riding an existing thread, still no --link-ask. The looked-up
    # thread_id is a full 36-char uuid so it skips the separate prefix-lookup
    # branch (len(thread) < 36), which would otherwise consume a 3rd fetchone.
    full_thread = "22222222-2222-2222-2222-222222222222"
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(full_thread,), (2, full_thread)])
    bs.send("orch-console", "cc-irsyad", "decision", "s",
            "x" * bs._MIN_BODY_BYTES, "P1", req=True, reply_to=99,
            dsn="postgresql://unused")
    assert not any("operator_asks" in e[0].lower() for e in cur.executed)


# ---- undeliverable-recipient refusal (orch-console #50969, bus #50648/#50649/
# #50689/#50697): 4 real dead-letters from a previous console body "replying"
# to write-only producers (commitment-sweeper, programme-stall-guard) that have
# no live wake owner. A standing rule against this doesn't survive a context
# reset -- enforce it in code, in send() itself (the one place the SQL lives),
# so every shim that calls send() directly is covered too, not just the CLI.

@pytest.mark.parametrize("to", ["commitment-sweeper", "programme-stall-guard", "sla-watchdog"])
def test_undeliverable_true_for_write_only_producers(to):
    assert bs._undeliverable(to) is True


@pytest.mark.parametrize("to", ["musa", "cto-desktop"])
def test_undeliverable_exempts_operator_and_human_opened(to):
    assert bs._undeliverable(to) is False


@pytest.mark.parametrize("to", ["cc-irsyad", "cai", "orch-console", "cc-orchestrator"])
def test_undeliverable_false_for_real_wake_eligible_recipients(to):
    # cc-orchestrator is eligible only on the P0/P1+rr floor -- _undeliverable
    # checks on that most-permissive floor, so the hub must never be misread
    # as a dead-letter sink (mirrors fleet_health.py's _undeliverable exactly).
    assert bs._undeliverable(to) is False


def test_undeliverable_false_for_none_or_empty():
    assert bs._undeliverable(None) is False
    assert bs._undeliverable("") is False


def test_refuse_if_undeliverable_raises_for_write_only_producer():
    with pytest.raises(SystemExit, match="has no wake owner"):
        bs.refuse_if_undeliverable("commitment-sweeper")


def test_refuse_if_undeliverable_message_uses_generic_id_placeholder_without_reply_to():
    with pytest.raises(SystemExit, match=r"WHERE id=<its row id>"):
        bs.refuse_if_undeliverable("commitment-sweeper")


def test_refuse_if_undeliverable_message_uses_reply_to_id():
    with pytest.raises(SystemExit, match=r"WHERE id=50648"):
        bs.refuse_if_undeliverable("commitment-sweeper", reply_to=50648)


def test_refuse_if_undeliverable_passes_for_real_recipient():
    bs.refuse_if_undeliverable("cc-irsyad")  # must not raise


def test_refuse_if_undeliverable_allow_flag_warns_instead_of_raising():
    import io

    stream = io.StringIO()
    bs.refuse_if_undeliverable("commitment-sweeper", allow_undeliverable=True, stream=stream)
    out = stream.getvalue()
    assert "WARNING" in out
    assert "--allow-undeliverable set" in out


# ---- the refusal lives in send() itself, not just the CLI -- every shim
# (_bus_tmp.py, gated_inbound_digest.py, ddl_coverage_watchdog.py, ingest.py,
# operator_log.py) calls send() directly, bypassing main()'s argparse path.

def test_send_refuses_before_any_db_connect_for_write_only_to(monkeypatch):
    import psycopg2

    def _boom(*a, **k):
        raise AssertionError("psycopg2.connect must not be called when the refusal fires")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(SystemExit, match="has no wake owner"):
        bs.send("orch-console", "commitment-sweeper", "update", "s",
                "x" * bs._MIN_BODY_BYTES, "P2", dsn="postgresql://unused")


def test_send_reply_to_write_only_producer_still_refused_not_inserted(monkeypatch):
    # The exact shape of the 4 real dead-letters: --reply-to a row FROM a
    # write-only producer must not create a new row TO it either -- the
    # correct action is to stamp the ORIGINAL row (a plain UPDATE), which this
    # refusal's message points at, never a new INSERT.
    import psycopg2

    def _boom(*a, **k):
        raise AssertionError("psycopg2.connect must not be called when the refusal fires")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(SystemExit, match=r"WHERE id=50648"):
        bs.send("orch-console", "commitment-sweeper", "update", "s",
                "x" * bs._MIN_BODY_BYTES, "P2", reply_to=50648,
                dsn="postgresql://unused")


def test_send_allow_undeliverable_bypasses_refusal_and_inserts(monkeypatch):
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4242, "th-uuid-abc")])
    row_id, thread_id = bs.send(
        "orch-console", "commitment-sweeper", "update", "s",
        "x" * bs._MIN_BODY_BYTES, "P2", allow_undeliverable=True,
        dsn="postgresql://unused",
    )
    assert (row_id, thread_id) == (4242, "th-uuid-abc")
    assert conn.committed is True


def test_send_real_recipient_unaffected_by_the_new_guard(monkeypatch):
    cur, conn = _fake_send(monkeypatch, fetch_queue=[(4242, "th-uuid-abc")])
    row_id, thread_id = bs.send(
        "orch-console", "cc-irsyad", "update", "s",
        "x" * bs._MIN_BODY_BYTES, "P2", dsn="postgresql://unused",
    )
    assert (row_id, thread_id) == (4242, "th-uuid-abc")


# ---- --allow-undeliverable CLI flag + end-to-end through main()

def test_allow_undeliverable_flag_defaults_false():
    args = bs.build_parser().parse_args([
        "--to", "cc-orchestrator", "--type", "update",
        "--subject", "s", "--priority", "P1",
    ])
    assert args.allow_undeliverable is False


def test_allow_undeliverable_flag_parses():
    args = bs.build_parser().parse_args([
        "--to", "commitment-sweeper", "--type", "update",
        "--subject", "s", "--priority", "P2", "--allow-undeliverable",
    ])
    assert args.allow_undeliverable is True


def test_cli_refuses_write_only_to_via_send(monkeypatch):
    import io
    import psycopg2

    monkeypatch.setenv("CC_BASE_AGENT_ID", "orch-console")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    # not a BASE-id question (bus #49220) -- give that earlier check an empty
    # answer so it passes through to send(), where the refusal under test lives.
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: [])

    def _boom(*a, **k):
        raise AssertionError("psycopg2.connect must not be called when the refusal fires")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(SystemExit, match="has no wake owner"):
        bs.main([
            "--to", "commitment-sweeper", "--type", "decision", "--subject", "s",
            "--priority", "P1",
        ])


# ---- stamp-or-die + evidence flag (bus #58159 item 2, WARN mode -- the REFUSE
# flip is a separate PR after the 48h soak this logging exists to measure).

def test_log_warn_writes_a_jsonl_record(tmp_path, monkeypatch):
    import json

    log_path = tmp_path / "bus_send_warnings.log"
    monkeypatch.setattr(bs, "WARN_LOG_PATH", str(log_path))
    bs._log_warn("unevidenced_claim", mtype="update", subject="s", matched_keyword="clean")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["warn_type"] == "unevidenced_claim"
    assert record["mtype"] == "update"
    assert record["matched_keyword"] == "clean"
    assert "ts" in record


def test_log_warn_sets_600_perms_on_first_write(tmp_path, monkeypatch):
    import stat

    log_path = tmp_path / "sub" / "bus_send_warnings.log"
    monkeypatch.setattr(bs, "WARN_LOG_PATH", str(log_path))
    bs._log_warn("rr_without_reply_to", from_agent="a", to="b", open_rr_id=1)
    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_log_warn_never_raises_on_oserror(monkeypatch):
    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(bs.os, "makedirs", _boom)
    bs._log_warn("unevidenced_claim", mtype="update")  # must not raise


def test_open_rr_from_queries_expected_shape(monkeypatch):
    import datetime
    import psycopg2

    cur = _FakeCursor([(123, datetime.timedelta(hours=2))])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    result = bs._open_rr_from("cc-irsyad", "orch-console", dsn="postgresql://unused")

    assert result == (123, str(datetime.timedelta(hours=2)))
    sql, params = cur.executed[0]
    assert "requires_response=true" in sql
    assert "responded_at IS NULL" in sql
    assert params == ("cc-irsyad", "orch-console")


def test_open_rr_from_none_when_no_open_row(monkeypatch):
    import psycopg2

    cur = _FakeCursor([None])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    assert bs._open_rr_from("cc-irsyad", "orch-console", dsn="postgresql://unused") is None


def test_warn_if_answering_rr_without_reply_to_warns_and_logs(monkeypatch):
    logged = []
    monkeypatch.setattr(bs, "_open_rr_from", lambda to, addressed_to, dsn=None: (123, "2:00:00"))
    monkeypatch.setattr(bs, "_log_warn", lambda warn_type, **f: logged.append((warn_type, f)))

    import io
    stream = io.StringIO()
    bs.warn_if_answering_rr_without_reply_to("orch-console", "cc-irsyad", None, stream=stream)

    out = stream.getvalue()
    assert "WARNING" in out
    assert "id=123" in out
    assert "--reply-to" in out
    assert logged == [("rr_without_reply_to", {"from_agent": "orch-console", "to": "cc-irsyad", "open_rr_id": 123})]


def test_warn_if_answering_rr_without_reply_to_silent_with_reply_to(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("_open_rr_from must not be queried when --reply-to is given")

    monkeypatch.setattr(bs, "_open_rr_from", _boom)
    import io
    stream = io.StringIO()
    bs.warn_if_answering_rr_without_reply_to("orch-console", "cc-irsyad", 99, stream=stream)
    assert stream.getvalue() == ""


def test_warn_if_answering_rr_without_reply_to_silent_when_no_open_row(monkeypatch):
    monkeypatch.setattr(bs, "_open_rr_from", lambda to, addressed_to, dsn=None: None)
    import io
    stream = io.StringIO()
    bs.warn_if_answering_rr_without_reply_to("orch-console", "cc-irsyad", None, stream=stream)
    assert stream.getvalue() == ""


def test_warn_if_answering_rr_without_reply_to_degrades_on_db_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bs, "_open_rr_from", _boom)
    import io
    stream = io.StringIO()
    bs.warn_if_answering_rr_without_reply_to("orch-console", "cc-irsyad", None, stream=stream)
    assert "WARNING" in stream.getvalue()
    assert "could not run" in stream.getvalue()


@pytest.mark.parametrize("keyword,text", [
    ("identical", "the two files are identical"),
    ("verified", "verified against prod"),
    ("clean", "scan came back clean"),
    ("byte-identical", "outputs are byte-identical"),
    ("pass", "all tests PASS"),
])
def test_warn_if_unevidenced_claim_warns_on_each_keyword(keyword, text, monkeypatch):
    logged = []
    monkeypatch.setattr(bs, "_log_warn", lambda warn_type, **f: logged.append((warn_type, f)))
    import io
    stream = io.StringIO()
    bs.warn_if_unevidenced_claim("update", "s", text, None, stream=stream)
    out = stream.getvalue()
    assert "WARNING" in out
    assert "--evidence" in out
    assert logged and logged[0][0] == "unevidenced_claim"


def test_warn_if_unevidenced_claim_silent_with_evidence_present():
    import io
    stream = io.StringIO()
    bs.warn_if_unevidenced_claim("update", "s", "verified clean", "sha256:report.txt", stream=stream)
    assert stream.getvalue() == ""


def test_warn_if_unevidenced_claim_silent_without_a_claim_keyword():
    import io
    stream = io.StringIO()
    bs.warn_if_unevidenced_claim("update", "deploy status", "build is green", None, stream=stream)
    assert stream.getvalue() == ""


def test_warn_if_unevidenced_claim_silent_for_non_claiming_message_types():
    import io
    stream = io.StringIO()
    bs.warn_if_unevidenced_claim("question", "is this clean?", "verified identical?", None, stream=stream)
    assert stream.getvalue() == ""


def test_warn_if_unevidenced_claim_does_not_match_password_as_pass():
    import io
    stream = io.StringIO()
    bs.warn_if_unevidenced_claim("update", "s", "rotated the password successfully", None, stream=stream)
    assert stream.getvalue() == ""


def test_evidence_flag_parses():
    args = bs.build_parser().parse_args([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--evidence", "sha256:report.txt",
    ])
    assert args.evidence == "sha256:report.txt"


def test_evidence_flag_defaults_none():
    args = bs.build_parser().parse_args([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s", "--priority", "P1",
    ])
    assert args.evidence is None


def test_cli_warns_on_unevidenced_claim_in_dry_run(monkeypatch, capsys):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("verified clean, all " + "x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--dry-run",
    ])
    assert rc == 0
    assert "--evidence" in capsys.readouterr().err


def test_cli_rr_without_reply_to_check_runs_after_dry_run_never_touches_db(monkeypatch):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))

    def _boom(*a, **k):
        raise AssertionError("_open_rr_from must not run in --dry-run mode")

    monkeypatch.setattr(bs, "_open_rr_from", _boom)
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--dry-run",
    ])
    assert rc == 0


def test_cli_warns_on_rr_without_reply_to_before_sending(monkeypatch):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "orch-console")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: [])
    monkeypatch.setattr(bs, "_open_rr_from", lambda to, addressed_to, dsn=None: (55, "1:00:00"))
    monkeypatch.setattr(bs, "send", lambda *a, **k: (4242, "th-uuid"))

    rc = bs.main([
        "--to", "cc-irsyad", "--type", "update", "--subject", "s",
        "--priority", "P2",
    ])
    assert rc == 0


# ---- STALE-INSTANCE-vs-LIVE-BASE refusal (bus #58614 item 1): the mirror of the
# base-vs-instance guard above. Bus row #58568 (cc-irsyad-3 -> cc-quality-1, an
# urgent review request) was stranded because cc-quality-1 had gone stale while
# its base cc-quality was the live singleton -- a sender addressing a retired
# numbered instance id gets no warning. REFUSE and point at the live base,
# never silently forward (same shape as refuse_if_base_has_live_instances).

def test_agent_status_row_queries_base_agent_id_and_liveness(monkeypatch):
    import psycopg2

    cur = _FakeCursor([("cc-quality", False)])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    result = bs._agent_status_row("cc-quality-1", dsn="postgresql://unused")

    assert result == ("cc-quality", False)
    sql, params = cur.executed[0]
    assert "base_agent_id" in sql
    assert "status IN ('idle','working')" in sql
    assert params == ("cc-quality-1",)


def test_agent_status_row_returns_none_for_unknown_agent(monkeypatch):
    import psycopg2

    cur = _FakeCursor([None])
    conn = _FakeConn(cur)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)

    assert bs._agent_status_row("cc-ghost-1", dsn="postgresql://unused") is None


def test_refuse_if_instance_stale_and_base_live_raises_with_suggestion(monkeypatch):
    rows = [("cc-quality", False), ("cc-quality", True)]
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: rows.pop(0))
    monkeypatch.setattr(bs, "_heartbeat_age_str", lambda agent_id, dsn=None: "status=offline, heartbeat 1:00:00 ago")
    with pytest.raises(SystemExit, match="Use --to cc-quality instead"):
        bs.refuse_if_instance_stale_and_base_live("cc-quality-1")


def test_refuse_if_instance_stale_and_base_live_noop_when_instance_itself_live(monkeypatch):
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: ("cc-quality", True))
    bs.refuse_if_instance_stale_and_base_live("cc-quality-1")  # must not raise


def test_refuse_if_instance_stale_and_base_live_noop_when_to_is_the_base(monkeypatch):
    # a singleton/solo-coord row reports base_agent_id == its own agent_id.
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: ("cc-quality", False))
    bs.refuse_if_instance_stale_and_base_live("cc-quality")  # must not raise


def test_refuse_if_instance_stale_and_base_live_noop_when_base_also_stale(monkeypatch):
    rows = [("cc-quality", False), ("cc-quality", False)]
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: rows.pop(0))
    bs.refuse_if_instance_stale_and_base_live("cc-quality-1")  # must not raise


def test_refuse_if_instance_stale_and_base_live_noop_when_base_has_no_row(monkeypatch):
    rows = [("cc-quality", False), None]
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: rows.pop(0))
    bs.refuse_if_instance_stale_and_base_live("cc-quality-1")  # must not raise


def test_refuse_if_instance_stale_and_base_live_noop_when_no_status_row(monkeypatch):
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: None)
    bs.refuse_if_instance_stale_and_base_live("cc-ghost-1")  # must not raise


def test_cli_refuses_on_stale_instance_with_live_base_before_sending(monkeypatch):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-3")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: [])
    rows = [("cc-quality", False), ("cc-quality", True)]
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: rows.pop(0))
    monkeypatch.setattr(bs, "_heartbeat_age_str", lambda agent_id, dsn=None: "status=offline, heartbeat 1:00:00 ago")

    def _boom(*a, **k):
        raise AssertionError("send() must not be called when the refusal fires")

    monkeypatch.setattr(bs, "send", _boom)
    with pytest.raises(SystemExit, match="Use --to cc-quality instead"):
        bs.main([
            "--to", "cc-quality-1", "--type", "review_request", "--subject", "s",
            "--priority", "P1",
        ])


def test_cli_reaches_send_when_instance_is_live(monkeypatch):
    import io

    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-3")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    monkeypatch.setattr(bs, "live_instance_ids", lambda to, dsn=None: [])
    monkeypatch.setattr(bs, "_agent_status_row", lambda *a, **k: ("cc-quality", True))
    # test_cli_reaches_send_when_instance_is_live targets the stale-instance
    # guard specifically -- stub the unrelated stamp-or-die RR check too, so
    # this test never attempts a real DB connect (bus #58685/op#22517-class
    # leak risk: dburl() prefers the real .env DATABASE_URL over any mock).
    monkeypatch.setattr(bs, "_open_rr_from", lambda *a, **k: None)
    monkeypatch.setattr(bs, "send", lambda *a, **k: (4242, "th-uuid"))

    rc = bs.main([
        "--to", "cc-quality-1", "--type", "review_request", "--subject", "s",
        "--priority", "P1",
    ])
    assert rc == 0
