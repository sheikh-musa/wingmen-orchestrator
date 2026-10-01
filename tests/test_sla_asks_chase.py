"""op#22669 "operator asks" chase net (piece 4, bus thread
d0533248-2d25-484f-84ad-3cdd08fe1fce): a waiting_on_operator operator_asks row
(migration 072) that goes overdue -- past its own chase_by, or past
ASKS_CHASE_GRACE_MIN since creation when no chase_by was set -- gets its OWNER
body (operator_asks.delegated_to) re-paged so a "WAITING ON MUSA" ask can never
just sit open forever with nobody prompted to go re-raise it with him. Mirrors
test_sla_aged_rr_repage.py's pure-logic, no-live-DB style function-for-function.
"""
import importlib
import time

w = importlib.import_module("scripts.priority_sla_watchdog")

MIN = 60.0


def row(id=1, delegated_to="cc-scholar", chase_by_epoch=None, created_epoch=0):
    return {
        "id": id,
        "ask": "ship the thing",
        "delegated_to": delegated_to,
        "chase_by_epoch": chase_by_epoch,
        "created_epoch": created_epoch,
    }


# ── selection predicate: overdue past chase_by, or past the grace window ─────
def test_row_with_chase_by_not_yet_due_is_not_a_target():
    now = 1000 * MIN
    r = row(chase_by_epoch=now - 10 * MIN)   # chase_by was 10m ago, grace is 30m
    assert w.asks_chase_targets([r], now=now, chase_state={}) == []


def test_row_with_chase_by_past_grace_is_a_target():
    now = 1000 * MIN
    r = row(id=5, chase_by_epoch=now - 31 * MIN)
    got = w.asks_chase_targets([r], now=now, chase_state={})
    assert [t["id"] for t in got] == [5]


def test_row_without_chase_by_uses_grace_since_created():
    now = 1000 * MIN
    # no chase_by set; created 31m ago (> default 30m grace) -> due
    r = row(id=6, chase_by_epoch=None, created_epoch=now - 31 * MIN)
    got = w.asks_chase_targets([r], now=now, chase_state={})
    assert [t["id"] for t in got] == [6]


def test_row_without_chase_by_under_grace_is_not_yet_due():
    now = 1000 * MIN
    r = row(id=7, chase_by_epoch=None, created_epoch=now - 5 * MIN)
    assert w.asks_chase_targets([r], now=now, chase_state={}) == []


def test_row_missing_both_timestamps_never_targets():
    now = 1000 * MIN
    r = row(id=8, chase_by_epoch=None, created_epoch=None)
    assert w.asks_chase_targets([r], now=now, chase_state={}) == []


# ── cadence: re-chase every ASKS_CHASE_EVERY_MIN, not every scan ─────────────
def test_within_cadence_of_last_chase_is_suppressed():
    now = 1000 * MIN
    state = {"5": now - 60 * MIN}   # last chased 1h ago; default cadence is 4h
    r = row(id=5, chase_by_epoch=now - 100 * MIN)
    assert w.asks_chase_targets([r], now=now, chase_state=state) == []


def test_at_cadence_boundary_second_chase_fires():
    now = 1000 * MIN
    state = {"5": now - 240 * MIN}   # exactly one cadence ago (default 240m)
    r = row(id=5, chase_by_epoch=now - 300 * MIN)
    got = w.asks_chase_targets([r], now=now, chase_state=state)
    assert [t["id"] for t in got] == [5]


# ── the action: capped, stamp-on-success-only, owner never the operator ──────
def test_chase_is_capped_per_scan():
    sent = {"n": 0}
    targets = [row(id=100 + i) for i in range(10)]
    state = {}
    n = w.chase_waiting_asks(
        targets, dry=False, now=1000 * MIN, chase_state=state,
        send_chase=lambda owner, t: sent.__setitem__("n", sent["n"] + 1) or True,
        max_chases=3,
    )
    assert n == 3 and sent["n"] == 3


def test_chase_stamps_state_only_on_success():
    state = {}
    w.chase_waiting_asks(
        [row(id=9)], dry=False, now=1000 * MIN, chase_state=state,
        send_chase=lambda owner, t: False,
    )
    assert "9" not in state, "failed chase must remain unstamped for retry"


def test_chase_stamps_state_on_success():
    state = {}
    now = 1000 * MIN
    w.chase_waiting_asks(
        [row(id=9)], dry=False, now=now, chase_state=state,
        send_chase=lambda owner, t: True,
    )
    assert state["9"] == now


def test_chase_dry_run_sends_nothing_and_stamps_nothing():
    sent = {"n": 0}
    state = {}
    w.chase_waiting_asks(
        [row(id=9)], dry=True, now=1000 * MIN, chase_state=state,
        send_chase=lambda owner, t: sent.__setitem__("n", sent["n"] + 1) or True,
    )
    assert sent["n"] == 0 and state == {}


def test_chase_targets_owner_never_falls_back_to_operator():
    # delegated_to missing entirely -> falls back to orch-console (a fleet body),
    # never anything resembling the operator/musa identity.
    sent = {}
    w.chase_waiting_asks(
        [row(id=9, delegated_to=None)], dry=False, now=1000 * MIN, chase_state={},
        send_chase=lambda owner, t: sent.setdefault("owner", owner) or True,
    )
    assert sent["owner"] == "orch-console"


# ── CONSTRAINT LOCK: reuse the same allowed-message-types check as ruling-3 ──
def test_asks_chase_message_type_is_constraint_valid():
    import os
    import re
    PINNED = {"review_request", "question", "decision", "agreed", "challenge",
              "update", "blocker", "counter"}
    dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    allowed = PINNED
    if dsn:
        try:
            import psycopg
            with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname='agent_messages_message_type_check'")
                r = cur.fetchone()
            if r:
                allowed = set(re.findall(r"'([a-z_]+)'::text", r[0]))
        except Exception:
            allowed = PINNED
    assert w.PAGE_MESSAGE_TYPE in allowed, (
        f"{w.PAGE_MESSAGE_TYPE!r} not in agent_messages_message_type_check — an armed "
        f"asks-chase would fail SILENTLY")


# ── migration 085 client-asks-chase net (Musa op#23944, bus #47110/#47114) ────
# Mirrors the waiting-on-operator suite above function-for-function; the one
# behavioral difference under test is that this net ALWAYS also pages
# orch-console (op#23944's finding was that nothing was watching these
# channels at all), and has no grace-since-created fallback (every
# client-channel row gets a REQUIRED chase_by at open time).

def client_row(id=1, delegated_to="cc-irsyad-coord", chase_by_epoch=None,
                created_epoch=0, committed_date=None):
    return {
        "id": id,
        "ask": "please add feature X",
        "delegated_to": delegated_to,
        "committed_date": committed_date,
        "chase_by_epoch": chase_by_epoch,
        "created_epoch": created_epoch,
    }


def test_client_row_not_yet_past_chase_by_is_not_a_target():
    now = 1000 * MIN
    r = client_row(chase_by_epoch=now + 10 * MIN)
    assert w.client_chase_targets([r], now=now, chase_state={}) == []


def test_client_row_past_chase_by_is_a_target():
    now = 1000 * MIN
    r = client_row(id=5, chase_by_epoch=now - 1 * MIN)
    got = w.client_chase_targets([r], now=now, chase_state={})
    assert [t["id"] for t in got] == [5]


def test_client_row_with_no_chase_by_never_targets():
    # should never happen (chase_by is REQUIRED at open time) but a missing
    # value must fail closed to "not due", never crash the scan.
    now = 1000 * MIN
    r = client_row(id=6, chase_by_epoch=None)
    assert w.client_chase_targets([r], now=now, chase_state={}) == []


def test_client_row_within_cadence_of_last_chase_is_suppressed():
    now = 1000 * MIN
    state = {"5": now - 60 * MIN}
    r = client_row(id=5, chase_by_epoch=now - 100 * MIN)
    assert w.client_chase_targets([r], now=now, chase_state=state) == []


def test_client_chase_is_capped_per_scan():
    targets = [client_row(id=100 + i, chase_by_epoch=0) for i in range(10)]
    n = w.chase_client_asks(
        targets, dry=False, now=1000 * MIN, chase_state={},
        send_chase=lambda owner, t: True, max_chases=3,
    )
    assert n == 3


def test_client_chase_stamps_state_only_on_success():
    state = {}
    w.chase_client_asks(
        [client_row(id=9, chase_by_epoch=0)], dry=False, now=1000 * MIN, chase_state=state,
        send_chase=lambda owner, t: False,
    )
    assert "9" not in state


def test_client_chase_dry_run_sends_nothing_and_stamps_nothing():
    sent = {"n": 0}
    state = {}
    w.chase_client_asks(
        [client_row(id=9, chase_by_epoch=0)], dry=True, now=1000 * MIN, chase_state=state,
        send_chase=lambda owner, t: sent.__setitem__("n", sent["n"] + 1) or True,
    )
    assert sent["n"] == 0 and state == {}


def test_client_ask_chase_pages_both_owner_and_orch_console(monkeypatch):
    """The dual-recipient behavior lives in _send_client_ask_chase — verify it
    inserts an agent_messages row for BOTH the owning lane and orch-console,
    not just one (bus #47110 item 3b)."""
    sent_to = []

    class FakeCursor:
        def execute(self, sql, params=None):
            if "INSERT INTO agent_messages" in sql:
                sent_to.append(params[0])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    ok = w._send_client_ask_chase(FakeConn(), "cc-irsyad-coord",
                                   client_row(id=42, created_epoch=time.time() - 60))
    assert ok is True
    assert set(sent_to) == {"cc-irsyad-coord", "orch-console"}


def test_client_ask_chase_owner_is_orch_console_deduplicates_to_one_send(monkeypatch):
    sent_to = []

    class FakeCursor:
        def execute(self, sql, params=None):
            if "INSERT INTO agent_messages" in sql:
                sent_to.append(params[0])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

    monkeypatch.setattr(w, "dry_identity_guard", lambda conn: True)
    ok = w._send_client_ask_chase(FakeConn(), "orch-console", client_row(id=43, delegated_to="orch-console"))
    assert ok is True
    assert sent_to == ["orch-console"]


# ── bus #47267 item 2: _gated_dry() observe-first default-OFF (both switches) ─
def test_gated_dry_defaults_true_when_asks_chase_env_unset(monkeypatch):
    monkeypatch.delenv("SLA_ASKS_CHASE_ENABLED", raising=False)
    assert w._gated_dry(False, "SLA_ASKS_CHASE_ENABLED") is True


def test_gated_dry_false_when_asks_chase_env_is_exact_1(monkeypatch):
    monkeypatch.setenv("SLA_ASKS_CHASE_ENABLED", "1")
    assert w._gated_dry(False, "SLA_ASKS_CHASE_ENABLED") is False


def test_gated_dry_stays_true_for_non_exact_1_values_asks_chase(monkeypatch):
    for v in ("true", "TRUE", "yes", "0", ""):
        monkeypatch.setenv("SLA_ASKS_CHASE_ENABLED", v)
        assert w._gated_dry(False, "SLA_ASKS_CHASE_ENABLED") is True


def test_gated_dry_defaults_true_when_client_asks_chase_env_unset(monkeypatch):
    monkeypatch.delenv("SLA_CLIENT_ASKS_CHASE_ENABLED", raising=False)
    assert w._gated_dry(False, "SLA_CLIENT_ASKS_CHASE_ENABLED") is True


def test_gated_dry_false_when_client_asks_chase_env_is_exact_1(monkeypatch):
    monkeypatch.setenv("SLA_CLIENT_ASKS_CHASE_ENABLED", "1")
    assert w._gated_dry(False, "SLA_CLIENT_ASKS_CHASE_ENABLED") is False


def test_gated_dry_manual_dry_run_wins_even_when_armed(monkeypatch):
    monkeypatch.setenv("SLA_ASKS_CHASE_ENABLED", "1")
    assert w._gated_dry(True, "SLA_ASKS_CHASE_ENABLED") is True


# ── bus #47349: _fetch_client_chase_asks must filter on triage_state ─────────
# A not_an_ask row stays OPEN (closed_at IS NULL) by design -- the heuristic
# classifier's call is reversible via asks_triage.py -- but 193 such rows were
# found live still carrying a chase_by with no triage_state filter in this
# query at all. These round-trip against the real schema (operator_ledger_db,
# tests/conftest.py) end-to-end through _fetch_client_chase_asks ->
# client_chase_targets, never a live store.

def test_not_an_ask_past_chase_by_does_not_page_end_to_end(operator_ledger_db):
    """test (c)-1: a not_an_ask row past its chase_by must never reach the
    due-check, let alone page."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, ask_surface, triage_state, chase_by) "
            "VALUES ('lol thanks', 'client-channel', 'not_an_ask', now() - interval '1 hour')"
        )
        c.commit()
    with psycopg.connect(operator_ledger_db) as conn:
        rows = w._fetch_client_chase_asks(conn)
    targets = w.client_chase_targets(rows, now=time.time(), chase_state={})
    assert targets == []


def test_ask_past_chase_by_pages_end_to_end(operator_ledger_db):
    """test (c)-2: a genuine ask row past its chase_by must still page."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, ask_surface, triage_state, delegated_to, chase_by) "
            "VALUES ('please ship it', 'client-channel', 'ask', 'cc-irsyad-coord', "
            "        now() - interval '1 hour') RETURNING id"
        )
        rid = cur.fetchone()[0]
        c.commit()
    with psycopg.connect(operator_ledger_db) as conn:
        rows = w._fetch_client_chase_asks(conn)
    targets = w.client_chase_targets(rows, now=time.time(), chase_state={})
    assert [t["id"] for t in targets] == [rid]


def test_captured_past_chase_by_pages_end_to_end(operator_ledger_db):
    """triage_state defaults to 'captured' (an untriaged row pending human
    review) -- the chase net must still watch it, same as 'ask'; only
    'not_an_ask' is excluded."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, ask_surface, delegated_to, chase_by) "
            "VALUES ('untriaged row', 'client-channel', 'cc-irsyad-coord', "
            "        now() - interval '1 hour') RETURNING id"
        )
        rid = cur.fetchone()[0]
        c.commit()
    with psycopg.connect(operator_ledger_db) as conn:
        rows = w._fetch_client_chase_asks(conn)
    targets = w.client_chase_targets(rows, now=time.time(), chase_state={})
    assert [t["id"] for t in targets] == [rid]


def test_retriaged_ask_after_not_an_ask_pages_again_end_to_end(operator_ledger_db):
    """test (c)-3: re-triaging a heuristic miss from not_an_ask back to ask
    restores the chase (scripts/asks_triage.py's restore-on-reopen), not a
    permanently-excluded row."""
    import psycopg
    from scripts import asks_triage as at
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks (ask, ask_surface, triage_state, delegated_to, chase_by) "
            "VALUES ('please ship it', 'client-channel', 'not_an_ask', 'cc-irsyad-coord', NULL) "
            "RETURNING id"
        )
        rid = cur.fetchone()[0]
        c.commit()
    at.triage(rid, "ask", summary="actually a real request", dsn=operator_ledger_db)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        # the restore sets a fresh (not-yet-due) window -- backdate it here to
        # exercise the due-check, same as a client ask that's been open a while.
        cur.execute("UPDATE operator_asks SET chase_by = now() - interval '1 hour' WHERE id=%s", (rid,))
        c.commit()
    with psycopg.connect(operator_ledger_db) as conn:
        rows = w._fetch_client_chase_asks(conn)
    targets = w.client_chase_targets(rows, now=time.time(), chase_state={})
    assert [t["id"] for t in targets] == [rid]
