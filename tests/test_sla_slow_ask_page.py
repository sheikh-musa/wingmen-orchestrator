"""op#34803 part B slow-ask page (Musa op#34841, console #78229/#78235): an open
operator ask with no op#-citing lane subject within SLOW_ASK_ACK_MIN (default
20) pages orch-console ONCE, deduped on operator_asks.slow_paged_at. Mirrors
test_sla_asks_chase.py's pure-logic, no-live-DB style.
"""
import importlib

w = importlib.import_module("scripts.priority_sla_watchdog")

MIN = 60.0
NOW = 100_000 * MIN


def row(id=1, op=34800, age_min=25, acked=False, slow_paged_at=None, lane="cc-scholar"):
    return {"id": id, "op": op, "delegated_to": lane, "summary": "ship the thing",
            "created_epoch": NOW - age_min * MIN, "acked": acked,
            "slow_paged_at": slow_paged_at}


def ids(rows):
    return [r["id"] for r in rows]


# ── the threshold is a config constant, defaulting to Musa's 20 min ──────────
def test_default_ack_threshold_is_20_minutes_not_2h():
    assert w.SLOW_ASK_ACK_MIN == 20


def test_threshold_is_env_overridable(monkeypatch):
    monkeypatch.setenv("SLA_SLOW_ASK_ACK_MIN", "5")
    w2 = importlib.reload(w)
    try:
        assert w2.SLOW_ASK_ACK_MIN == 5
    finally:
        monkeypatch.delenv("SLA_SLOW_ASK_ACK_MIN")
        importlib.reload(w)


# ── selection ────────────────────────────────────────────────────────────────
def test_unacked_ask_under_threshold_is_not_a_target():
    assert w.slow_ask_targets([row(age_min=19)], now=NOW) == []


def test_unacked_ask_past_threshold_is_a_target():
    assert ids(w.slow_ask_targets([row(id=7, age_min=21)], now=NOW)) == [7]


def test_acked_ask_is_never_a_target():
    assert w.slow_ask_targets([row(age_min=60, acked=True)], now=NOW) == []


def test_already_paged_ask_is_never_repaged():
    assert w.slow_ask_targets([row(age_min=60, slow_paged_at="2026-10-27T15:00Z")], now=NOW) == []


def test_ask_older_than_horizon_is_excluded_so_arming_cannot_flood_the_backlog():
    assert w.slow_ask_targets([row(age_min=w.SLOW_ASK_HORIZON_MIN + 1)], now=NOW) == []


def test_unowned_ask_still_pages_no_lane_is_no_ack():
    assert ids(w.slow_ask_targets([row(id=3, lane=None)], now=NOW)) == [3]


def test_decimal_epoch_from_postgres_extract_is_handled():
    # extract(epoch FROM ...) comes back as decimal.Decimal; float - Decimal raises
    from decimal import Decimal
    r = row(id=9)
    r["created_epoch"] = Decimal(str(NOW - 25 * MIN))
    assert ids(w.slow_ask_targets([r], now=NOW)) == [9]


def test_custom_threshold_is_honoured():
    assert ids(w.slow_ask_targets([row(id=4, age_min=3)], now=NOW, ack_min=2)) == [4]


# ── paging: dry-run, cap, dead-man's switch ──────────────────────────────────
def test_dry_run_sends_nothing():
    calls = []
    n = w.page_slow_asks([row()], dry=True, send_page=lambda t: calls.append(t) or True)
    assert n == 0 and calls == []


def test_per_scan_cap_holds_the_rest():
    calls = []
    n = w.page_slow_asks([row(id=i) for i in range(5)], dry=False, max_pages=2,
                         send_page=lambda t: calls.append(t["id"]) or True)
    assert n == 2 and calls == [0, 1]


def test_failed_page_is_not_counted_and_next_target_still_tried():
    calls = []

    def send(t):
        calls.append(t["id"])
        return t["id"] != 1

    n = w.page_slow_asks([row(id=1), row(id=2)], dry=False, send_page=send)
    assert n == 1 and calls == [1, 2]


# ── _send_slow_ask_page: one tx — lock, send, stamp; rollback on failure ─────
class FakeCur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self._last = sql

    def fetchone(self):
        return (1,) if self.conn.lock_ok else None


class FakeConn:
    def __init__(self, lock_ok=True):
        self.lock_ok = lock_ok
        self.sql, self.committed, self.rolled_back = [], 0, 0

    def cursor(self):
        return FakeCur(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_send_pages_console_and_stamps_in_same_tx():
    conn, sent = FakeConn(), []

    def send(frm, to, mtype, subj, body, prio):
        sent.append((frm, to, mtype, subj, prio))
        return 99, "thread"

    assert w._send_slow_ask_page(conn, row(id=5, op=34841), send=send) is True
    (frm, to, mtype, subj, prio), = sent
    assert (frm, to, prio) == ("cc-fleet-health", "orch-console", "P1")
    assert mtype == w.PAGE_MESSAGE_TYPE
    assert subj.startswith(w.SLOW_ASK_SUBJECT_PREFIX) and "op#34841" in subj
    assert any("FOR UPDATE SKIP LOCKED" in s for s in conn.sql)
    assert any("SET slow_paged_at" in s for s in conn.sql)
    assert conn.committed == 1 and conn.rolled_back == 0


def test_send_failure_rolls_back_and_leaves_ask_unstamped():
    conn = FakeConn()

    def send(*a):
        raise SystemExit("bus_send: REFUSED")

    assert w._send_slow_ask_page(conn, row(), send=send) is False
    assert conn.committed == 0 and conn.rolled_back == 1
    assert not any("SET slow_paged_at" in s for s in conn.sql)


def test_already_stamped_or_locked_row_is_skipped_without_sending():
    conn, sent = FakeConn(lock_ok=False), []
    assert w._send_slow_ask_page(conn, row(), send=lambda *a: sent.append(a)) is False
    assert sent == [] and conn.committed == 0


def test_page_subject_cannot_count_as_its_own_ack():
    # the fetch SQL excludes subjects with this prefix; the page subject must carry it
    assert w.SLOW_ASK_SUBJECT_PREFIX == "[slow-ask]"


def test_interim_delegation_window_defaults_to_15_minutes():
    # console #78298 ruling B: a delegation row to the lane within 15 min of the ask,
    # plus a lane reply in its thread, counts as an ack until every subject cites the op#
    assert w.SLOW_ASK_DELEGATION_WINDOW_MIN == 15


# ── console #78426: exempt console's OWN ledger, never an unrouted ask ───────
def test_undelegated_ask_with_no_held_commitment_still_pages():
    r = row(id=11, lane=None)
    r["console_held"] = False
    assert ids(w.slow_ask_targets([r], now=NOW)) == [11]


def test_console_held_timed_reminder_is_exempt_like_ask_5430():
    r = row(id=5430, lane=None)
    r["console_held"] = True        # held_commitment 727, owner orch-console, pending
    assert w.slow_ask_targets([r], now=NOW) == []


def test_ask_delegated_to_console_itself_is_exempt():
    assert w.slow_ask_targets([row(id=12, lane="orch-console")], now=NOW) == []
