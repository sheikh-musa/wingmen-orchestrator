"""Tests for disk_autoremediate — tiered pre-crash disk reclaim (cc-fleet-health §4.6).

TDD for the build committed after the 2026-08-28 ~7h fleet outage (disk free hit ~0 →
Errno 28 crashed every tmux lane; the disk monitor ALERTED but auto-remediated NOTHING).
These tests pin the SAFETY-CRITICAL behavior: tier boundaries, early-stop (don't over-reclaim),
held-path exclusion, dry-run touches nothing, and fail-loud-but-continue across reclaimers.
"""
from __future__ import annotations

import os
import sys
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import disk_autoremediate as dar  # noqa: E402

GB = 1 << 30


# ---- decide(): tier boundaries -------------------------------------------------
def test_decide_ok_when_free_above_remediate_threshold():
    assert dar.decide(50 * GB, remediate_under=20 * GB, floor=8 * GB) == "ok"

def test_decide_remediate_at_and_below_threshold():
    assert dar.decide(20 * GB, remediate_under=20 * GB, floor=8 * GB) == "remediate"
    assert dar.decide(19 * GB, remediate_under=20 * GB, floor=8 * GB) == "remediate"
    assert dar.decide(1 * GB, remediate_under=20 * GB, floor=8 * GB) == "remediate"

def test_decide_boundary_just_above_is_ok():
    # 20GB+1 byte free is still OK — strictly-less-than triggers.
    assert dar.decide(20 * GB + 1, remediate_under=20 * GB, floor=8 * GB) == "ok"


# ---- run_reclaimers(): early-stop, isolation, aggregation ----------------------
def _mk_reclaimer(name, freed, *, raises=False):
    """A fake reclaimer: reports `freed` bytes; optionally raises to test isolation."""
    calls = {"applied": 0}
    def _apply(dry_run):
        calls["applied"] += 1
        if raises:
            raise RuntimeError(f"{name} boom")
        return freed
    return {"name": name, "apply": _apply, "_calls": calls}


def test_run_reclaimers_stops_early_once_target_met():
    # free starts at 10GB, target 20GB. First reclaimer frees 12GB → 22GB ≥ target → STOP.
    r1 = _mk_reclaimer("a", 12 * GB)
    r2 = _mk_reclaimer("b", 99 * GB)
    freed_seq = [10 * GB, 22 * GB]  # probe returns after each apply
    def probe():
        return freed_seq.pop(0)
    res = dar.run_reclaimers([r1, r2], free_probe=probe, target=20 * GB, dry_run=False)
    assert r1["_calls"]["applied"] == 1
    assert r2["_calls"]["applied"] == 0, "must not run further reclaimers once target met"
    assert res["stopped_early"] is True


def test_run_reclaimers_runs_all_when_target_never_met():
    r1 = _mk_reclaimer("a", 1 * GB)
    r2 = _mk_reclaimer("b", 1 * GB)
    probes = [5 * GB, 6 * GB, 7 * GB]
    res = dar.run_reclaimers([r1, r2], free_probe=lambda: probes.pop(0),
                             target=50 * GB, dry_run=False)
    assert r1["_calls"]["applied"] == 1 and r2["_calls"]["applied"] == 1
    assert res["stopped_early"] is False


def test_run_reclaimers_isolates_a_failing_reclaimer_and_continues():
    # A reclaimer that raises must NOT abort the sweep — it's logged as an error and the
    # next reclaimer still runs (independent + additive; disk emergency wants max reclaim).
    r1 = _mk_reclaimer("boom", 0, raises=True)
    r2 = _mk_reclaimer("good", 3 * GB)
    probes = [5 * GB, 5 * GB, 8 * GB]
    res = dar.run_reclaimers([r1, r2], free_probe=lambda: probes.pop(0),
                             target=50 * GB, dry_run=False)
    assert r2["_calls"]["applied"] == 1, "failing reclaimer must not block the next one"
    assert any(e["name"] == "boom" for e in res["errors"]), "failure must be recorded loudly"


def test_run_reclaimers_dry_run_passes_flag_through():
    seen = {}
    def _apply(dry_run):
        seen["dry_run"] = dry_run
        return 0
    r = {"name": "x", "apply": _apply}
    dar.run_reclaimers([r], free_probe=lambda: 100 * GB, target=1 * GB, dry_run=True)
    assert seen["dry_run"] is True


# ---- next_cache_candidates(): held-path exclusion + age gate -------------------
def test_next_cache_candidates_excludes_held_paths(tmp_path):
    root = tmp_path / "projects"
    keep = root / "cosem-video-pipeline" / "build" / ".next" / "cache"
    take = root / "ihsanos" / ".next" / "cache"
    for d in (keep, take):
        d.mkdir(parents=True)
        (d / "blob").write_bytes(b"x" * 1024)
    old = time.time() - 3600  # 60min ago
    for d in (keep, take):
        os.utime(d, (old, old))
    cands = dar.next_cache_candidates(root, exclude={"cosem-video-pipeline"},
                                      older_than_s=1800, now=time.time())
    paths = {str(p) for p in cands}
    assert str(take) in paths
    assert str(keep) not in paths, "held cosem-video path must be excluded"


def test_next_cache_candidates_skips_recently_modified(tmp_path):
    root = tmp_path / "projects"
    fresh = root / "ihsanos" / ".next" / "cache"
    fresh.mkdir(parents=True)
    (fresh / "blob").write_bytes(b"x" * 1024)
    # mtime = now → younger than the 30min gate → NOT a candidate (may be an active build)
    cands = dar.next_cache_candidates(root, exclude=set(), older_than_s=1800, now=time.time())
    assert str(fresh) not in {str(p) for p in cands}


# ---- CONCRETE reclaimers mutate NOTHING under dry_run (Nazim review 34985 follow-up) ----
# Pins what was verified by hand: dry-run is honored at the LEAF, not just the top — a reclaimer
# in dry_run must never rmtree/subprocess, only measure. Guards against a future regression that
# arms a destructive command behind a dry-run flag that's checked too late.
def test_reclaim_dir_cmd_dry_run_never_runs_the_command(tmp_path):
    size_dir = tmp_path / "cache"
    size_dir.mkdir()
    (size_dir / "blob").write_bytes(b"x" * 2048)
    marker = tmp_path / "COMMAND_RAN"          # the command would create this IF executed
    apply = dar._reclaim_dir_cmd(str(size_dir), ["touch", str(marker)])
    freed = apply(dry_run=True)
    assert not marker.exists(), "dry_run must NOT run the reclaim subprocess"
    assert freed >= 2048, "dry_run still reports would-free size"


def test_reclaim_next_cache_dry_run_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    d = root / "ihsanos" / ".next" / "cache"
    d.mkdir(parents=True)
    (d / "blob").write_bytes(b"x" * 4096)
    old = time.time() - 7200                    # older than the 60min age gate → a candidate
    os.utime(d, (old, old))
    monkeypatch.setattr(dar, "PROJECTS_ROOT", root)
    monkeypatch.setattr(dar, "EXCLUDE_PROJECTS", set())
    monkeypatch.setattr(dar, "NEXT_CACHE_MIN_AGE_S", 3600)
    freed = dar._reclaim_next_cache(dry_run=True)
    assert d.exists() and (d / "blob").exists(), "dry_run must NOT rmtree the cache dir"
    assert freed >= 4096, "dry_run still reports would-free size"
