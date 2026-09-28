"""commitment_sweeper hub wake-floor enforcement (bus #44527).

fleet-health's RCA (#44526): the hub's wake floor is P0/P1 AND
requires_response. A DUE commitment addressed to cc-orchestrator (#44508)
landed P1 + requires_response=FALSE -- below the floor -- and the hub sat
idle ~14h never seeing it. This test drives sweep() against a fully mocked
connection (no live DSN, nothing on the substrate) and asserts the
agent_messages row commitment_sweeper writes for a due commitment owned by
cc-orchestrator is P1 + requires_response=True.
"""
import datetime as dt

import pytest

from nervous_system import commitment_sweeper as cs


class _FakeCursor:
    def __init__(self, due_row, live_rows):
        self._due_row = due_row
        self._live_rows = live_rows
        self._last = []
        self.inserts = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        q = " ".join(query.split())
        if "FROM agent_status" in q:
            self._last = self._live_rows
        elif "FROM held_commitments" in q and q.strip().startswith("SELECT"):
            self._last = [self._due_row] if self._due_row else []
        elif "INSERT INTO agent_messages" in q:
            self.inserts.append(params)
            self._last = [{"id": 999}]
        else:
            self._last = []

    def fetchall(self):
        return list(self._last)

    def fetchone(self):
        return self._last[0] if self._last else None


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def due_commitment_owned_by_hub():
    return {
        "id": 55,
        "owner_agent": "cc-orchestrator",
        "title": "test commitment",
        "payload": "do the thing",
        "due_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10),
        "status": "pending",
        "source_ref": "bus #1",
        "channel_tag": None,
        "fired_at": None,
    }


def test_due_commitment_owned_by_hub_fires_p1_and_requires_response(due_commitment_owned_by_hub, monkeypatch):
    fake_cur = _FakeCursor(due_commitment_owned_by_hub, live_rows=[{"agent_id": "cc-orchestrator"}])
    fake_conn = _FakeConn(fake_cur)
    monkeypatch.setattr(cs, "_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(cs.psycopg, "connect", lambda *a, **k: fake_conn)
    monkeypatch.setattr(cs._sl, "hub_alive_evidence", lambda: True)

    rc = cs.sweep(fire=True)

    assert rc == 0
    assert len(fake_cur.inserts) == 1
    to_agent, subject, body, requires_response, priority = fake_cur.inserts[0]
    assert to_agent == "cc-orchestrator"
    assert priority == "P1"
    assert requires_response is True
