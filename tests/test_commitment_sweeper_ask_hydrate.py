"""hc#581 (console #78229/#78250): every open operator ask with a committed_date gets ONE
held_commitment owned by orch-console, hydrated at the top of each commitment sweep, so a
lane that misses its date pages the console at the deadline. Pure-logic tests, no live DB.
"""
import datetime as dt

from nervous_system import commitment_sweeper as cs

UTC = dt.timezone.utc


def ask(id=1, committed=dt.datetime(2026, 10, 27, tzinfo=UTC), closed_at=None,
        triage_state="ask", closed_reason=None):
    return {"id": id, "op": 34000 + id, "delegated_to": "cc-scholar", "summary": "ship it",
            "committed_date": committed, "closed_at": closed_at, "closed_reason": closed_reason,
            "triage_state": triage_state}


def hc(id=500, ask_id=1, status="pending", due=dt.datetime(2026, 10, 27, 20, tzinfo=UTC)):
    return {"id": id, "source_ref": f"operator_asks:{ask_id}", "status": status, "due_at": due}


# ── deadline: 00:00:00Z is a DATE -> end of that day in Asia/Dubai (UTC+4) ───
def test_midnight_utc_means_end_of_day_dubai():
    assert cs.ask_deadline(dt.datetime(2026, 10, 27, tzinfo=UTC)) == \
        dt.datetime(2026, 10, 27, 20, tzinfo=UTC)


def test_any_other_time_is_an_exact_deadline():
    t = dt.datetime(2026, 10, 27, 19, 59, tzinfo=UTC)
    assert cs.ask_deadline(t) == t


def test_non_utc_aware_input_is_normalised():
    t = dt.datetime(2026, 10, 28, 4, 0, tzinfo=dt.timezone(dt.timedelta(hours=4)))  # = 00:00Z
    assert cs.ask_deadline(t) == dt.datetime(2026, 10, 28, 20, tzinfo=UTC)


# ── plan ─────────────────────────────────────────────────────────────────────
def kinds(plan):
    return [(k, (o["id"])) for k, o, _ in plan]


def test_open_ask_without_commitment_gets_one_inserted_at_its_deadline():
    plan = cs.plan_ask_commitments([ask(id=7)], [])
    assert kinds(plan) == [("insert", 7)]
    assert plan[0][2] == dt.datetime(2026, 10, 27, 20, tzinfo=UTC)


def test_existing_pending_at_same_deadline_is_left_alone():
    assert cs.plan_ask_commitments([ask()], [hc()]) == []


def test_moved_committed_date_reschedules_the_pending_row():
    a = ask(committed=dt.datetime(2026, 10, 29, tzinfo=UTC))
    plan = cs.plan_ask_commitments([a], [hc(id=9)])
    assert kinds(plan) == [("reschedule", 9)]
    assert plan[0][2] == dt.datetime(2026, 10, 29, 20, tzinfo=UTC)


def test_closed_ask_cancels_pending_and_fired_rows_never_discharges():
    a = ask(closed_at=dt.datetime(2026, 10, 27, 12, tzinfo=UTC), closed_reason="delivered")
    plan = cs.plan_ask_commitments([a], [hc(id=1, status="pending"), hc(id=2, status="fired")])
    assert kinds(plan) == [("cancel", 1), ("cancel", 2)]
    assert all("closed (delivered)" in reason for _, _, reason in plan)


def test_retriaged_ask_cancels_its_row():
    plan = cs.plan_ask_commitments([ask(triage_state="done")], [hc(id=3)])
    assert kinds(plan) == [("cancel", 3)]


def test_closed_ask_with_no_active_row_is_a_noop():
    a = ask(closed_at=dt.datetime(2026, 10, 27, tzinfo=UTC))
    assert cs.plan_ask_commitments([a], [hc(status="cancelled")]) == []


def test_fired_row_is_not_reinserted_for_the_same_deadline():
    assert cs.plan_ask_commitments([ask()], [hc(status="fired")]) == []


def test_recommit_to_later_date_after_fire_cancels_and_reinserts():
    a = ask(committed=dt.datetime(2026, 10, 30, tzinfo=UTC))
    assert kinds(cs.plan_ask_commitments([a], [hc(id=4, status="fired")])) == \
        [("cancel", 4), ("insert", 1)]


def test_discharged_row_blocks_reinsert_unless_recommitted_later():
    assert cs.plan_ask_commitments([ask()], [hc(status="discharged")]) == []
    later = ask(committed=dt.datetime(2026, 10, 31, tzinfo=UTC))
    assert kinds(cs.plan_ask_commitments([later], [hc(status="discharged")])) == [("insert", 1)]


def test_rows_from_other_sources_are_ignored():
    other = {"id": 8, "source_ref": "bus #1", "status": "pending",
             "due_at": dt.datetime(2026, 1, 1, tzinfo=UTC)}
    assert kinds(cs.plan_ask_commitments([ask(id=1)], [other])) == [("insert", 1)]


def test_ask_ids_are_not_prefix_matched():
    # operator_asks:1 must not match operator_asks:12's row
    assert kinds(cs.plan_ask_commitments([ask(id=1)], [hc(id=5, ask_id=12)])) == [("insert", 1)]


# ── hydrate: own tx, console-owned row, rollback on dry-run ──────────────────
class _Cur:
    def __init__(self, asks, existing):
        self.asks, self.existing, self.sql = asks, existing, []
        self._last = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, params=None):
        self.sql.append((" ".join(q.split()), params))
        if "FROM operator_asks" in q:
            self._last = self.asks
        elif q.lstrip().startswith("SELECT") and "FROM held_commitments" in q:
            self._last = self.existing
        else:
            self._last = []

    def fetchall(self):
        return list(self._last)


class _Conn:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_hydrate_inserts_console_owned_row_and_commits_on_fire():
    cur = _Cur([ask(id=7)], [])
    conn = _Conn(cur)
    assert cs.hydrate_ask_commitments(conn, fire=True) == 1
    (q, params), = [s for s in cur.sql if s[0].startswith("INSERT INTO held_commitments")]
    owner, title, payload, due, source_ref, tag, created_by = params
    assert owner == "orch-console" and source_ref == "operator_asks:7"
    assert due == dt.datetime(2026, 10, 27, 20, tzinfo=UTC)
    assert "op#34007" in title and created_by == cs.FROM_AGENT
    import json
    assert json.loads(payload)["ask_id"] == 7  # held_commitments_payload_valid_json
    assert " id," not in q.split("VALUES")[0]  # never write an explicit id
    assert conn.commits == 1 and conn.rollbacks == 0


def test_hydrate_dry_run_rolls_back():
    conn = _Conn(_Cur([ask(id=7)], []))
    cs.hydrate_ask_commitments(conn, fire=False)
    assert conn.commits == 0 and conn.rollbacks == 1


def test_hydrate_failure_does_not_block_core_sweep_and_exits_nonzero(monkeypatch):
    calls = []

    def boom(*a, **k):
        raise RuntimeError("hydrate db down")

    monkeypatch.setattr(cs, "_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(cs, "hydrate_ask_commitments", boom)
    monkeypatch.setattr(cs.psycopg, "connect", lambda *a, **k: _Conn(_Cur([], [])))
    monkeypatch.setattr(cs, "_sweep_due", lambda dsn, fire: calls.append(fire) or 0)
    assert cs.sweep(fire=True) == 3
    assert calls == [True]
