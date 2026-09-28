"""programme_stall_guard: the invariant 'every open programme has a live checkpoint' (offline)."""
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

import psycopg

_spec = importlib.util.spec_from_file_location(
    "programme_stall_guard", Path(__file__).resolve().parent.parent / "scripts" / "programme_stall_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def prog(pid, status="in_progress", ask="test programme"):
    return {"id": pid, "status": status, "updated_at": T0, "ask": ask}


def cp(bid, status="pending", discharged_at=None, note=None, payload=None, cid=1, owner="cc-example", title="CHECKPOINT"):
    return {"id": cid, "owner_agent": owner, "title": title,
            "payload": payload if payload is not None else f'{{"backlog_id": {bid}}}',
            "status": status, "discharged_at": discharged_at, "discharge_note": note}


class FindUnclockedTest(unittest.TestCase):
    def test_open_programme_without_any_checkpoint_is_unclocked(self):
        self.assertEqual([p["id"] for p in guard.find_unclocked([prog(1)], [])], [1])

    def test_pending_or_fired_checkpoint_keeps_it_clocked(self):
        self.assertEqual(guard.find_unclocked([prog(1), prog(2)], [cp(1), cp(2, "fired")]), [])

    def test_only_discharged_or_cancelled_checkpoints_means_unclocked(self):
        # the classic stall: the last step was done, and nobody armed the next one
        got = guard.find_unclocked([prog(1), prog(2)], [cp(1, "discharged"), cp(2, "cancelled")])
        self.assertEqual([p["id"] for p in got], [1, 2])

    def test_parked_and_done_programmes_are_ignored(self):
        self.assertEqual(guard.find_unclocked([prog(1, "parked"), prog(2, "done"), prog(3, "dropped")], []), [])

    def test_needs_you_counts_as_open(self):
        self.assertEqual([p["id"] for p in guard.find_unclocked([prog(4, "needs_you")], [])], [4])

    def test_checkpoint_for_a_different_programme_does_not_count(self):
        self.assertEqual([p["id"] for p in guard.find_unclocked([prog(1)], [cp(2)])], [1])

    def test_malformed_or_missing_payload_never_counts_as_a_clock(self):
        for bad in ("", "not json", '{"other": 1}', '{"backlog_id": "x"}', None):
            with self.subTest(payload=bad):
                self.assertEqual(len(guard.find_unclocked([prog(1)], [cp(1, payload=bad or "")])), 1)


class LastProgressTest(unittest.TestCase):
    def test_latest_discharged_checkpoint_wins(self):
        a = T0 + dt.timedelta(days=1)
        b = T0 + dt.timedelta(days=3)
        when, note = guard.last_progress(prog(1), [cp(1, "discharged", a, "old"), cp(1, "discharged", b, "new")])
        self.assertEqual((when, note), (b, "new"))

    def test_falls_back_to_programme_updated_at(self):
        self.assertEqual(guard.last_progress(prog(1), [cp(1)]), (T0, ""))


class CorruptPayloadTest(unittest.TestCase):
    """bus #44697: a payload that fails to parse as JSON at all is CORRUPT, distinct from a
    well-formed payload that simply has no backlog_id (not corrupt, just unlinked)."""

    def test_unparseable_json_is_corrupt(self):
        for bad in ("not json", '{"backlog_id": 1', "{backlog_id: 1}"):
            with self.subTest(payload=bad):
                self.assertTrue(guard.is_corrupt_payload(bad))

    def test_wellformed_json_without_backlog_id_is_not_corrupt(self):
        for ok in ('{"other": 1}', '{"backlog_id": "x"}', "{}"):
            with self.subTest(payload=ok):
                self.assertFalse(guard.is_corrupt_payload(ok))

    def test_absent_payload_is_not_corrupt(self):
        for absent in ("", None):
            with self.subTest(payload=absent):
                self.assertFalse(guard.is_corrupt_payload(absent))

    def test_backlog_id_of_still_returns_none_for_corrupt_payload(self):
        self.assertIsNone(guard.backlog_id_of("not json"))


class FindCorruptCheckpointsTest(unittest.TestCase):
    def test_live_checkpoint_with_unparseable_payload_is_flagged(self):
        c = cp(1, "pending", payload="not json", cid=57, owner="cc-irsyad-coord")
        self.assertEqual(guard.find_corrupt_checkpoints([c]), [c])

    def test_discharged_checkpoint_with_bad_payload_is_not_flagged(self):
        # only LIVE (pending/fired) checkpoints matter — a discharged one is already done
        c = cp(1, "discharged", payload="not json", cid=57)
        self.assertEqual(guard.find_corrupt_checkpoints([c]), [])

    def test_live_checkpoint_with_valid_payload_is_not_flagged(self):
        self.assertEqual(guard.find_corrupt_checkpoints([cp(1, "fired")]), [])


class _FakeConn:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))


def _run_check(monkeypatch, tmp_path, programmes, commitments, state=None):
    """check()'s live INSERT path, DB fully mocked (no real DSN, nothing on the substrate)."""
    fake_conn = _FakeConn()
    monkeypatch.setattr(guard, "_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(guard, "_load", lambda: (programmes, commitments))
    monkeypatch.setattr(guard, "_read_state", lambda: dict(state or {}))
    monkeypatch.setattr(guard, "STATE", tmp_path / "programme_stall_guard_test.json")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: fake_conn)
    rc = guard.check(dry_run=False)
    assert rc == 0
    return fake_conn


def test_escalation_signs_as_programme_stall_guard_not_orch_console(monkeypatch, tmp_path):
    fake_conn = _run_check(monkeypatch, tmp_path, [prog(1)], [])
    assert len(fake_conn.executed) == 1
    _, params = fake_conn.executed[0]
    from_agent, subject, body = params
    assert from_agent == "programme-stall-guard"
    assert from_agent != "orch-console", (
        "bus #44697: a STALLED-BY-CONSTRUCTION row must not misattribute itself as having "
        "come from orch-console"
    )


def test_corrupt_checkpoint_is_named_explicitly_in_the_body(monkeypatch, tmp_path):
    corrupt_cp = cp(1, "pending", payload="not json", cid=57, owner="cc-irsyad-coord",
                     title="CHECKPOINT backlog#35")
    fake_conn = _run_check(monkeypatch, tmp_path, [], [corrupt_cp])
    assert len(fake_conn.executed) == 1
    _, params = fake_conn.executed[0]
    _, subject, body = params
    assert "checkpoint #57" in body
    assert "invalid payload" in body
    assert "invalid payload" in subject


def test_nothing_due_makes_no_insert(monkeypatch, tmp_path):
    fake_conn = _run_check(monkeypatch, tmp_path, [], [])
    assert fake_conn.executed == []


def test_state_keys_for_programmes_and_checkpoints_do_not_collide(monkeypatch, tmp_path):
    # a checkpoint id and a programme id can share the same integer — the "cp#" prefix
    # keeps their 24h re-escalation clocks independent.
    corrupt_cp = cp(1, "pending", payload="not json", cid=1)
    fake_conn = _run_check(monkeypatch, tmp_path, [prog(1)], [corrupt_cp])
    assert len(fake_conn.executed) == 1
    import json
    saved = json.loads(guard.STATE.read_text())
    assert "1" in saved
    assert "cp#1" in saved


if __name__ == "__main__":
    unittest.main()
