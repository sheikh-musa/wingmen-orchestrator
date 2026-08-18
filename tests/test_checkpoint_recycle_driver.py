"""Phase 2 — the hands-off checkpoint-recycle DRIVER (op#14539 / bus #27720 item-1).

drive_checkpoint_recycle() automates the loop the SRE ran BY HAND for cc-irsyad #27711:
durable bus note -> nudge the lane to self-handoff -> block-wait for a FRESH, non-stub
handoff -> re-verify gates at the moment of action (freshness measured against T_nudge,
the Phase-1 fix) -> recycle via the PROVEN gated recycler -> return outcome.

Every side-effecting collaborator is dependency-injected so the whole pipeline is unit-
testable with NO real tmux / DB / clock / subprocess. These tests encode the SAFETY
invariants (fail-closed, dry-run touches nothing, singleton unreachable, dead-man's-switch).
"""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))
import sre_lane_recycle as slr            # noqa: E402
import fleet_health_boundaries as fhb     # noqa: E402
import checkpoint_recycle_driver as drv   # noqa: E402


# ── fakes ─────────────────────────────────────────────────────────────────────
class Clock:
    """Shared mutable clock: now_fn reads it, sleep_fn advances it (no real time)."""
    def __init__(self, t0=1000.0):
        self.t = t0
        self.sleeps = []
    def now(self):
        return self.t
    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Spy:
    def __init__(self, ret=None, raises=None):
        self.calls = []
        self.ret = ret
        self.raises = raises
    def __call__(self, *a, **k):
        self.calls.append((a, k))
        if self.raises is not None:
            raise self.raises
        return self.ret


def _worker_row():
    return {"lane": "irsyad", "base_agent_id": "cc-irsyad",
            "tmux_session": "irsyad", "worktree_path": "/tmp/wt", "notes": None}


@pytest.fixture()
def gates_pass(monkeypatch):
    """Entry gates idle+git_clean True (unit-isolate the driver's orchestration from the
    real tmux/git probes, which have their own tests)."""
    monkeypatch.setattr(slr, "gate_idle", lambda session: True)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt: True)


def _fresh_handoff_file(tmp_path, mtime, size):
    p = tmp_path / "irsyad-handoff-NOW.md"
    p.write_text("x" * size)
    os.utime(p, (mtime, mtime))
    return str(p)


# ── safety floor at entry ───────────────────────────────────────────────────
def test_singleton_target_aborts_and_never_recycles():
    row = {"base_agent_id": "cai", "tmux_session": "cai", "worktree_path": "/tmp/wt"}
    recycle = Spy()
    clock = Clock()
    out = drv.drive_checkpoint_recycle(
        None, row, armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle)
    assert out["outcome"] == "aborted"
    assert "singleton" in out["reason"].lower()
    assert recycle.calls == []


def test_not_idle_aborts_no_side_effects(monkeypatch):
    monkeypatch.setattr(slr, "gate_idle", lambda session: False)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt: True)
    recycle, nudge, note = Spy(), Spy(ret=0), Spy()
    clock = Clock()
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=nudge, post_note_fn=note, recycle_fn=recycle)
    assert out["outcome"] == "aborted"
    assert "idle" in out["reason"].lower()
    assert recycle.calls == [] and nudge.calls == [] and note.calls == []


def test_dirty_git_aborts_no_recycle(monkeypatch):
    monkeypatch.setattr(slr, "gate_idle", lambda session: True)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt: False)
    recycle = Spy()
    clock = Clock()
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle)
    assert out["outcome"] == "aborted"
    assert "git" in out["reason"].lower() or "clean" in out["reason"].lower()
    assert recycle.calls == []


# ── dry-run touches nothing ─────────────────────────────────────────────────
def test_dry_run_touches_nothing(gates_pass):
    recycle, nudge, note = Spy(), Spy(ret=0), Spy()
    clock = Clock()
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=False, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=nudge, post_note_fn=note, recycle_fn=recycle)
    assert out["outcome"] == "dry-run"
    assert recycle.calls == [] and nudge.calls == [] and note.calls == []


# ── happy path armed ────────────────────────────────────────────────────────
def test_armed_happy_path_recycles_with_t_nudge(gates_pass, monkeypatch, tmp_path):
    # plan()'s own fresh_handoff check reads slr._newest_handoff_mtime; make it fresh.
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)
    monkeypatch.setattr(slr, "last_material_action_epoch", lambda conn, base: 900.0)
    clock = Clock(t0=1000.0)
    # handoff_path_fn: absent on 1st poll, present (fresh + big) on 2nd
    handoff = _fresh_handoff_file(tmp_path, mtime=2000.0, size=1200)
    seq = [None, handoff]
    hp = Spy()
    def handoff_path_fn(base, notes):
        return seq.pop(0) if seq else handoff
    recycle = Spy(ret={"recycled": True, "resume_verified": True})
    nudge, note = Spy(ret=0), Spy()
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=nudge, post_note_fn=note, recycle_fn=recycle,
        handoff_path_fn=handoff_path_fn)
    assert out["outcome"] == "recycled"
    assert note.calls and nudge.calls               # note + nudge happened
    assert len(recycle.calls) == 1
    (args, _kw) = recycle.calls[0]
    assert args[0] == "cc-irsyad"                   # base
    assert args[2] == 1000.0                        # t_nudge (clock at nudge stage)


def test_done_ack_race_does_not_block_recycle(gates_pass, monkeypatch, tmp_path):
    """The lane posts a DONE ack AFTER writing its handoff (last_material_action > handoff
    mtime). Passing handoff_ref_epoch=t_nudge means plan still permits -> recycle proceeds."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 1_000.0)   # handoff @1000
    monkeypatch.setattr(slr, "last_material_action_epoch", lambda conn, base: 9_999.0)  # late DONE ack
    clock = Clock(t0=999.0)                          # t_nudge=999 < handoff mtime 1000
    handoff = _fresh_handoff_file(tmp_path, mtime=1000.0, size=1200)
    recycle = Spy(ret={"recycled": True})
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: handoff)
    assert out["outcome"] == "recycled"
    assert len(recycle.calls) == 1


# ── dead-man's-switch: no fresh handoff / stub / recycle failure => abort LOUD ─
def test_handoff_never_appears_times_out_no_recycle(gates_pass):
    recycle = Spy()
    clock = Clock(t0=1000.0)
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: None, wait_s=240, poll_s=15)
    assert out["outcome"] == "aborted"
    assert recycle.calls == []
    assert clock.sleeps                              # it actually polled


def test_stub_handoff_below_floor_is_not_ready(gates_pass, tmp_path):
    recycle = Spy()
    clock = Clock(t0=1000.0)
    stub = _fresh_handoff_file(tmp_path, mtime=2000.0, size=100)   # < MIN_HANDOFF_BYTES
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: stub, wait_s=60, poll_s=15)
    assert out["outcome"] == "aborted"
    assert recycle.calls == []


def test_stale_handoff_before_t_nudge_is_not_ready(gates_pass, tmp_path):
    """A handoff written BEFORE we nudged (mtime < t_nudge) is the OLD one — not ready."""
    recycle = Spy()
    clock = Clock(t0=1000.0)
    stale = _fresh_handoff_file(tmp_path, mtime=500.0, size=1200)  # mtime < t_nudge
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: stale, wait_s=60, poll_s=15)
    assert out["outcome"] == "aborted"
    assert recycle.calls == []


def test_nudge_failure_aborts_no_recycle(gates_pass):
    recycle = Spy()
    clock = Clock()
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=3), post_note_fn=Spy(), recycle_fn=recycle,   # rc=3 => not verified
        handoff_path_fn=lambda b, n: None)
    assert out["outcome"] == "aborted"
    assert "nudge" in out["reason"].lower()
    assert recycle.calls == []


def test_recycle_raises_aborts_loud(gates_pass, monkeypatch, tmp_path):
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)
    monkeypatch.setattr(slr, "last_material_action_epoch", lambda conn, base: 900.0)
    clock = Clock(t0=1000.0)
    handoff = _fresh_handoff_file(tmp_path, mtime=2000.0, size=1200)
    recycle = Spy(raises=RuntimeError("reset_lane.sh rc=1"))
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: handoff)
    assert out["outcome"] == "aborted"
    assert "reset_lane" in out["reason"] or "rc=1" in out["reason"]
