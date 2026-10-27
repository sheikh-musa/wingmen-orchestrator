"""scripts/bus_watch.py -- the ONE sanctioned bus-watch loop (console #78501/#78695).

A hand-rolled psycopg poll loop inside cosem-platform-4's session never committed
and held a transaction open ~2d21h. bus_watch.py polls on an AUTOCOMMIT connection,
over instance + base + '*', one line per new row. Pure-logic tests, no live DB.
"""
import importlib

bw = importlib.import_module("scripts.bus_watch")


def test_instance_watches_itself_its_base_and_broadcast():
    assert bw.recipients("cc-cosem-platform-4") == ["cc-cosem-platform-4", "cc-cosem-platform", "*"]


def test_base_watches_itself_and_broadcast_only():
    assert bw.recipients("orch-console") == ["orch-console", "*"]


def test_one_line_per_row_carries_id_priority_type_sender_subject():
    line = bw.format_row({"id": 78501, "priority": "P2", "message_type": "update",
                          "from_agent": "orch-console", "subject": "Idle-in-tx source\nfound"})
    assert line == "#78501 P2 update orch-console: Idle-in-tx source found"


class FakeCur:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None
        self.description = [("id",), ("priority",), ("message_type",), ("from_agent",), ("subject",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows, autocommit=True):
        self.cur, self.autocommit = FakeCur(rows), autocommit

    def cursor(self):
        return self.cur


def test_fetch_new_returns_rows_after_the_watermark():
    conn = FakeConn([(5, "P1", "decision", "orch-console", "go")])
    rows = bw.fetch_new(conn, ["cc-x-1", "cc-x", "*"], last_id=4)
    assert rows == [{"id": 5, "priority": "P1", "message_type": "decision",
                     "from_agent": "orch-console", "subject": "go"}]
    assert conn.cur.params == (4, ["cc-x-1", "cc-x", "*"])


def test_fetch_new_refuses_a_non_autocommit_connection():
    # the whole point: a poll on a non-autocommit connection is the 2d21h leak
    try:
        bw.fetch_new(FakeConn([], autocommit=False), ["cc-x"], last_id=0)
    except RuntimeError as e:
        assert "autocommit" in str(e)
    else:
        raise AssertionError("expected RuntimeError on a non-autocommit connection")
