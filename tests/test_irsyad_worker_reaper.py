"""Reaper safety proof (Nazim #40850 ruling 2). The reaper tears down WOUND-DOWN elastic workers
so the autoscaler pool shrinks — but it must NEVER reap a working worker, a standing lane, or a
worker with a pending order. The reap decision is a PURE function
`should_reap(session_is_worker, pane_busy, wound_down_past_grace, has_pending_order=False)` so the
required cases are unit-testable with no DB/tmux (same discipline as the autoscaler).

  pane_busy: True = working, False = idle, None = unknown (fail-safe).
  has_pending_order: True = an unacked order/claim is outstanding (bus #54606 idle-proof gap).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.irsyad_worker_reaper import is_worker_session, should_reap  # noqa: E402


# ── the four cases Nazim required ──────────────────────────────────────────────────────
def test_working_pane_is_never_reaped():
    # a busy (working) worker, even if it looks wound-down, is never reaped
    assert should_reap(session_is_worker=True, pane_busy=True, wound_down_past_grace=True) is False


def test_wind_down_without_grace_is_not_reaped():
    # idle worker, but wind-down NOT yet past the grace window -> left for possible re-tasking
    assert should_reap(session_is_worker=True, pane_busy=False, wound_down_past_grace=False) is False


def test_standing_lane_is_never_reaped_even_if_idle():
    # tabung / coord are not worker sessions -> never reapable, even idle + "wound down"
    assert is_worker_session("irsyad-tabung-jumaat") is False
    assert is_worker_session("irsyad-coord") is False
    assert should_reap(session_is_worker=False, pane_busy=False, wound_down_past_grace=True) is False


def test_idle_wound_down_worker_past_grace_is_reaped():
    # the reap case: a spun worker, idle, wound down past the grace -> reap (pool shrinks -> re-propose)
    assert is_worker_session("irsyad-worker-2") is True
    assert should_reap(session_is_worker=True, pane_busy=False, wound_down_past_grace=True) is True


# ── fail-safe: unknown pane state never reaps ──────────────────────────────────────────
def test_unknown_pane_state_is_never_reaped():
    # pane_busy None (couldn't read the footer / no such session) -> fail-safe, never reap
    assert should_reap(session_is_worker=True, pane_busy=None, wound_down_past_grace=True) is False


def test_worker_session_regex():
    assert is_worker_session("irsyad-worker-1") is True
    assert is_worker_session("irsyad-worker-13") is True
    assert is_worker_session("irsyad-worker-") is False   # no number
    assert is_worker_session("irsyad-workerx-1") is False
    assert is_worker_session("") is False


# ── idle-proof gap fix (bus #54606): a pending order blocks reap even if otherwise eligible ──
def test_pending_order_blocks_reap_even_when_otherwise_eligible():
    # idle + wound-down-past-grace would normally reap, but an unacked order addressed to it
    # (or a claimed-and-not-done coord_dispatch_queue row) must hold it back -> op#54606 fix
    assert should_reap(session_is_worker=True, pane_busy=False, wound_down_past_grace=True,
                        has_pending_order=True) is False


def test_no_pending_order_still_reaps():
    # default (no pending order) preserves prior behavior -> reap proceeds
    assert should_reap(session_is_worker=True, pane_busy=False, wound_down_past_grace=True,
                        has_pending_order=False) is True


def test_has_pending_order_default_preserves_prior_callers():
    # callers that don't pass has_pending_order (pre-#54606 call sites) are unaffected
    assert should_reap(session_is_worker=True, pane_busy=False, wound_down_past_grace=True) is True
