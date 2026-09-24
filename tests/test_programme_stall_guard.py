"""programme_stall_guard: the invariant 'every open programme has a live checkpoint' (offline)."""
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "programme_stall_guard", Path(__file__).resolve().parent.parent / "scripts" / "programme_stall_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def prog(pid, status="in_progress"):
    return {"id": pid, "status": status, "updated_at": T0}


def cp(bid, status="pending", discharged_at=None, note=None, payload=None):
    return {"payload": payload if payload is not None else f'{{"backlog_id": {bid}}}',
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


if __name__ == "__main__":
    unittest.main()
