"""op#22669 "operator asks" chase net (piece 4, bus thread
d0533248-2d25-484f-84ad-3cdd08fe1fce): a waiting_on_operator operator_asks row
(migration 072) that goes overdue -- past its own chase_by, or past
ASKS_CHASE_GRACE_MIN since creation when no chase_by was set -- gets its OWNER
body (operator_asks.delegated_to) re-paged so a "WAITING ON MUSA" ask can never
just sit open forever with nobody prompted to go re-raise it with him. Mirrors
test_sla_aged_rr_repage.py's pure-logic, no-live-DB style function-for-function.
"""
import importlib

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
