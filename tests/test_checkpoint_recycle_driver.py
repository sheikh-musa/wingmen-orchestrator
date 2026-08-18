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


_BRANCH = "feat/merge-people-app-layer"


def _fresh_handoff_file(tmp_path, mtime, size=None, text=None):
    """A handoff fixture. By default names the lane's branch (so the content check passes);
    pass text= to override (e.g. a stub that omits the branch), or size= for a raw-bytes blob."""
    p = tmp_path / "irsyad-handoff-NOW.md"
    if text is None:
        if size is not None:
            text = "x" * size
        else:
            text = f"# handoff\nbranch {_BRANCH}\n" + ("state line\n" * 100)
    p.write_text(text)
    os.utime(p, (mtime, mtime))
    return str(p)


def _ok_branch(_wt):
    return _BRANCH


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
    # handoff_path_fn: absent on 1st poll, present (fresh + big + names branch) on 2nd
    handoff = _fresh_handoff_file(tmp_path, mtime=2000.0)
    seq = [None, handoff]
    def handoff_path_fn(base, notes):
        return seq.pop(0) if seq else handoff
    recycle = Spy(ret={"recycled": True, "resume_verified": True})
    nudge, note = Spy(ret=0), Spy()
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=nudge, post_note_fn=note, recycle_fn=recycle,
        handoff_path_fn=handoff_path_fn, branch_fn=_ok_branch)
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
    handoff = _fresh_handoff_file(tmp_path, mtime=1000.0)   # names the branch
    recycle = Spy(ret={"recycled": True})
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: handoff, branch_fn=_ok_branch)
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
    handoff = _fresh_handoff_file(tmp_path, mtime=2000.0)   # names the branch
    recycle = Spy(raises=RuntimeError("reset_lane.sh rc=1"))
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: handoff, branch_fn=_ok_branch)
    assert out["outcome"] == "aborted"
    assert "reset_lane" in out["reason"] or "rc=1" in out["reason"]


# ── 3A: content verification — handoff must NAME the lane's current branch ─────
def test_content_ok_helper_requires_branch_present():
    assert drv._handoff_content_ok(f"...on branch {_BRANCH} now", _BRANCH) is True
    assert drv._handoff_content_ok("no branch named here", _BRANCH) is False
    assert drv._handoff_content_ok(f"text {_BRANCH}", None) is False     # undeterminable branch
    assert drv._handoff_content_ok(f"text {_BRANCH}", "") is False       # empty branch


def test_armed_aborts_when_handoff_omits_branch(gates_pass, monkeypatch, tmp_path):
    """A fresh, big-enough handoff that does NOT name the branch is a stub/garbage -> abort."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)
    monkeypatch.setattr(slr, "last_material_action_epoch", lambda conn, base: 900.0)
    clock = Clock(t0=1000.0)
    stub = _fresh_handoff_file(tmp_path, mtime=2000.0, text="# stub\n" + ("filler\n" * 200))
    recycle = Spy(ret={"recycled": True})
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: stub, branch_fn=_ok_branch)
    assert out["outcome"] == "aborted"
    assert "content" in out["reason"].lower() or "branch" in out["reason"].lower()
    assert recycle.calls == []


def test_armed_aborts_when_branch_undeterminable(gates_pass, monkeypatch, tmp_path):
    """branch_fn can't resolve a branch (detached HEAD / git error) -> can't verify -> abort."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)
    monkeypatch.setattr(slr, "last_material_action_epoch", lambda conn, base: 900.0)
    clock = Clock(t0=1000.0)
    handoff = _fresh_handoff_file(tmp_path, mtime=2000.0)
    recycle = Spy(ret={"recycled": True})
    out = drv.drive_checkpoint_recycle(
        None, _worker_row(), armed=True, now_fn=clock.now, sleep_fn=clock.sleep,
        nudge_fn=Spy(ret=0), post_note_fn=Spy(), recycle_fn=recycle,
        handoff_path_fn=lambda b, n: handoff, branch_fn=lambda wt: None)
    assert out["outcome"] == "aborted"
    assert recycle.calls == []


# ── 3B: resume verification — context genuinely DROPPED after the /clear ───────
def test_verify_resume_true_when_context_dropped():
    assert drv.verify_resume("cc-irsyad", lambda base: 0.09, max_frac=0.5) is True


def test_verify_resume_false_when_still_high():
    assert drv.verify_resume("cc-irsyad", lambda base: 0.93, max_frac=0.5) is False


def test_verify_resume_false_when_unmeasurable():
    assert drv.verify_resume("cc-irsyad", lambda base: None, max_frac=0.5) is False


# ── 3C: production entrypoint run() — dry-run is DB-free + touches nothing ─────
def test_run_dry_run_outcome(monkeypatch):
    """run() in dry-run: discovers the lane, passes entry gates, returns dry-run, and NEVER
    constructs a real side effect firing (armed=False short-circuits)."""
    monkeypatch.setattr(drv, "discover_session_lane", lambda conn, session, **k: _worker_row())
    monkeypatch.setattr(slr, "gate_idle", lambda session: True)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt: True)

    class Args:
        session = "irsyad"; arm = False; wait_s = 240; poll_s = 15
        reason = "test"
    out = drv.run(Args(), conn=None)
    assert out["outcome"] == "dry-run"


# ── Phase 3.5: per-session discovery with GROUND-TRUTH worktree ───────────────
# Fixes the DISTINCT-ON-base bug: for a multi-session base (cc-irsyad), discovery must pair
# a SESSION with ITS OWN live cwd so idle(session) + git_clean(worktree) refer to one lane.
class _FakeCur:
    def __init__(self, results):
        self._results = list(results); self._i = 0; self.executed = []
    def execute(self, q, params=None):
        self.executed.append((q, params)); self._cur = self._results[self._i]; self._i += 1
    def fetchone(self):
        return self._cur
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, results): self._cur = _FakeCur(results)
    def cursor(self): return self._cur


def test_discover_session_lane_pairs_session_with_its_own_cwd():
    """lane==session AND worktree==the session's LIVE cwd — so both gates target one lane."""
    conn = _FakeConn([("cc-irsyad",), None])            # agent_status base; no fleet_lanes row
    row = drv.discover_session_lane(conn, "irsyad-tabung-jumaat", cwd_fn=lambda s: "/wt/tabung")
    assert row == {"lane": "irsyad-tabung-jumaat", "base_agent_id": "cc-irsyad",
                   "tmux_session": "irsyad-tabung-jumaat", "worktree_path": "/wt/tabung", "notes": None}


def test_discover_session_lane_singleton_base_refused():
    sing = sorted(fhb.SINGLETON_BODIES)[0]
    conn = _FakeConn([(sing,)])
    assert drv.discover_session_lane(conn, "somesess", cwd_fn=lambda s: "/wt/x") is None


def test_discover_session_lane_dead_session_none():
    assert drv.discover_session_lane(_FakeConn([None]), "gone", cwd_fn=lambda s: "/wt/x") is None


def test_discover_session_lane_unreadable_cwd_fails_closed():
    conn = _FakeConn([("cc-irsyad",)])
    assert drv.discover_session_lane(conn, "sess", cwd_fn=lambda s: None) is None


def test_discover_session_lane_worktree_disagreement_fails_closed():
    """fleet_lanes names a DIFFERENT worktree than the live cwd -> ambiguous -> None. This is
    the exact cc-irsyad session/worktree mismatch that would recycle the wrong tree."""
    conn = _FakeConn([("cc-irsyad",), ("/wt/OTHER", "n")])
    assert drv.discover_session_lane(conn, "irsyad-tabung-jumaat", cwd_fn=lambda s: "/wt/tabung") is None


def test_discover_session_lane_agreement_keeps_notes():
    conn = _FakeConn([("cc-irsyad",), ("/wt/tabung", "handoff_glob=reports/x-*.md")])
    row = drv.discover_session_lane(conn, "irsyad-tabung-jumaat", cwd_fn=lambda s: "/wt/tabung")
    assert row is not None
    assert row["notes"] == "handoff_glob=reports/x-*.md" and row["worktree_path"] == "/wt/tabung"
