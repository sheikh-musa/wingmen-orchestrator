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


# ---- module-default retune (2026-10-22, bus #73391) ----------------------------
def test_default_floor_matches_real_crash_margin_not_healthy_baseline():
    # This Mini's steady-state free sits at ~7-9GB once live lanes are up (not a crash
    # risk); the old FLOOR=8GB paged P1 on every tick at that baseline with 0GB
    # reclaimable. 6GB matches the HARD_FREE_GB floor retuned in file_history_prune.py
    # (PR#463) and disk_space_monitor.sh (PR#464) — the actual near-[Errno 28] margin.
    assert dar.FLOOR == 6 * GB
    assert dar.REMEDIATE_UNDER == 20 * GB  # unchanged: cheap to attempt reclaim broadly


# ---- pager-routing retune (2026-10-22, bus #73430) ------------------------------
def test_default_emergency_floor_is_4gb():
    assert dar.EMERGENCY_FLOOR == 4 * GB


def test_page_severity_log_not_loud_on_reclaimer_error_with_healthy_free():
    # RETUNED 2026-10-22 (bus #73901 false P1): a reclaimer error used to ALWAYS page loud
    # regardless of free space -- but a healthy disk (well above floor) with one failed
    # reclaimer is not a disk emergency; it's a tooling bug, log/warn not P1. Decoupled from
    # the actual danger signal (free_after vs floor), same shape as the managed-band split.
    assert dar.page_severity(50 * GB, [{"name": "x", "error": "boom"}],
                             floor=6 * GB, emergency_floor=4 * GB) == "log"


def test_page_severity_loud_on_reclaimer_error_when_also_below_floor():
    # An error COMBINED with an actual floor breach is still the genuine emergency case.
    assert dar.page_severity(5 * GB, [{"name": "x", "error": "boom"}],
                             floor=6 * GB, emergency_floor=4 * GB) == "loud"
    assert dar.page_severity(3 * GB, [{"name": "x", "error": "boom"}],
                             floor=6 * GB, emergency_floor=4 * GB) == "loud"


def test_page_severity_loud_when_free_probe_unreadable():
    # Can't confirm safety -> fail loud, not silent.
    assert dar.page_severity(None, [], floor=6 * GB, emergency_floor=4 * GB) == "loud"


def test_page_severity_loud_below_emergency_floor():
    assert dar.page_severity(3 * GB, [], floor=6 * GB, emergency_floor=4 * GB) == "loud"
    assert dar.page_severity(4 * GB - 1, [], floor=6 * GB, emergency_floor=4 * GB) == "loud"


def test_page_severity_log_only_in_managed_band():
    # Below floor but at/above the emergency line: real breach, no new action -- log-only.
    assert dar.page_severity(4 * GB, [], floor=6 * GB, emergency_floor=4 * GB) == "log"
    assert dar.page_severity(5 * GB, [], floor=6 * GB, emergency_floor=4 * GB) == "log"
    assert dar.page_severity(6 * GB - 1, [], floor=6 * GB, emergency_floor=4 * GB) == "log"


def test_page_loud_false_never_attempts_db_write(monkeypatch, capsys):
    # bus #73447: the managed band must be LOG-ONLY -- not even a lower-priority bus row,
    # since the console's doorbell rings on any new unread row regardless of priority.
    import psycopg2
    def _boom(*a, **k):
        raise AssertionError("loud=False must never attempt a DB connection")
    monkeypatch.setattr(psycopg2, "connect", _boom)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    summary = {"results": [{"name": "x", "freed": 0}], "errors": []}
    dar._page(10 * GB, 5 * GB, summary, loud=False)
    err = capsys.readouterr().err
    assert "log-only, no bus row" in err


def test_page_loud_true_still_attempts_db_write(monkeypatch, capsys):
    import psycopg2
    calls = {"connect": 0}
    class _FakeCursor:
        def execute(self, *a, **k): pass
    class _FakeConn:
        def cursor(self): return _FakeCursor()
        def commit(self): pass
    def _fake_connect(*a, **k):
        calls["connect"] += 1
        return _FakeConn()
    monkeypatch.setattr(psycopg2, "connect", _fake_connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    summary = {"results": [], "errors": []}
    dar._page(10 * GB, 5 * GB, summary, loud=True)
    assert calls["connect"] == 1, "loud=True must still attempt the bus row"


def test_page_severity_none_at_or_above_floor_with_no_errors():
    assert dar.page_severity(6 * GB, [], floor=6 * GB, emergency_floor=4 * GB) == "none"
    assert dar.page_severity(20 * GB, [], floor=6 * GB, emergency_floor=4 * GB) == "none"


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


def test_reclaim_dir_cmd_measures_disk_free_delta_when_flagged(tmp_path, monkeypatch):
    # Simulates brew cleanup -s (bus #73431): its real effect (pruning old Cellar versions)
    # isn't reflected in the measured cache dir's size, but IS reflected in real disk free.
    size_dir = tmp_path / "cache"
    size_dir.mkdir()
    (size_dir / "blob").write_bytes(b"x" * 1024)  # cache dir size unchanged by the "cleanup"
    marker = tmp_path / "RAN"
    disk_free_seq = [10 * GB, 11 * GB]  # +1GB freed elsewhere (e.g. Cellar), not in size_dir
    monkeypatch.setattr(dar, "_disk_free", lambda *a, **k: disk_free_seq.pop(0))
    apply = dar._reclaim_dir_cmd(str(size_dir), ["touch", str(marker)],
                                 measure_via_disk_free=True)
    freed = apply(dry_run=False)
    assert marker.exists()
    assert freed == 1 * GB, "must report the real disk-free delta, not the unchanged dir-size delta"


def test_reclaim_dir_cmd_disk_free_flag_does_not_affect_dry_run(tmp_path, monkeypatch):
    size_dir = tmp_path / "cache"
    size_dir.mkdir()
    (size_dir / "blob").write_bytes(b"x" * 2048)
    marker = tmp_path / "RAN"
    apply = dar._reclaim_dir_cmd(str(size_dir), ["touch", str(marker)],
                                 measure_via_disk_free=True)
    freed = apply(dry_run=True)
    assert not marker.exists(), "dry_run must still never run the command"
    assert freed >= 2048, "dry_run still estimates via dir size (no delta to measure)"


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


# ---- daily_backup_candidates(): keep newest N, pure ----------------------------
# bus #73708 (2026-10-22): orch-console hand-pruned ~/wingmen/backups (kept last 3 days)
# as part of the disk-crash-averted fix and asked for it as a standing reclaimer tier.
def test_daily_backup_candidates_keeps_newest_n(tmp_path):
    root = tmp_path / "backups"
    for name in ("2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"):
        (root / name).mkdir(parents=True)
    cands = dar.daily_backup_candidates(root, keep_n=3)
    names = {p.name for p in cands}
    assert names == {"2026-10-18", "2026-10-19"}, "must prune only the oldest, keeping newest 3"


def test_daily_backup_candidates_empty_when_at_or_under_keep_n(tmp_path):
    root = tmp_path / "backups"
    for name in ("2026-10-21", "2026-10-22"):
        (root / name).mkdir(parents=True)
    cands = dar.daily_backup_candidates(root, keep_n=3)
    assert cands == [], "fewer dirs than keep_n -> nothing to prune"


def test_daily_backup_candidates_missing_root_returns_empty(tmp_path):
    cands = dar.daily_backup_candidates(tmp_path / "does-not-exist", keep_n=3)
    assert cands == []


def test_reclaim_daily_backups_dry_run_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    for name in ("2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"):
        d = root / name
        d.mkdir(parents=True)
        (d / "snap.tar").write_bytes(b"x" * 4096)
    monkeypatch.setattr(dar, "BACKUPS_ROOT", root)
    monkeypatch.setattr(dar, "BACKUP_KEEP_DAYS", 3)
    freed = dar._reclaim_daily_backups(dry_run=True)
    assert (root / "2026-10-18").exists(), "dry_run must NOT rmtree an old backup dir"
    assert freed >= 4096 * 2, "dry_run still reports would-free size for the 2 oldest"


def test_reclaim_daily_backups_apply_prunes_only_oldest(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    for name in ("2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"):
        d = root / name
        d.mkdir(parents=True)
        (d / "snap.tar").write_bytes(b"x" * 4096)
    monkeypatch.setattr(dar, "BACKUPS_ROOT", root)
    monkeypatch.setattr(dar, "BACKUP_KEEP_DAYS", 3)
    freed = dar._reclaim_daily_backups(dry_run=False)
    remaining = {p.name for p in root.iterdir()}
    assert remaining == {"2026-10-20", "2026-10-21", "2026-10-22"}, "must keep exactly the newest 3"
    assert freed >= 4096 * 2


# ---- library_caches_candidates() + _reclaim_library_caches(): permission-aware ----
# bus #73708: orch-console hand-cleared ~2.9GB from ~/Library/Caches (regenerable per
# macOS convention) and asked for it as a standing reclaimer. Several subdirs are
# TCC/SIP-protected even for the owning user (CloudKit, HomeKit, Safari, etc) -- a
# permission error on one entry must be skipped+logged, never abort the sweep or
# count as a reclaimer "error" (that's an EXPECTED, already-diagnosed condition on
# this Mini, not a new/unknown failure -- raising here would re-introduce exactly
# the perpetual-noise pager pattern bus #73430/#73447 already fixed elsewhere).
def test_library_caches_candidates_lists_top_level_entries(tmp_path):
    root = tmp_path / "Caches"
    (root / "Homebrew").mkdir(parents=True)
    (root / "com.apple.python").mkdir(parents=True)
    cands = library_caches_names = {p.name for p in dar.library_caches_candidates(root)}
    assert cands == {"Homebrew", "com.apple.python"}


def test_library_caches_candidates_missing_root_returns_empty(tmp_path):
    cands = dar.library_caches_candidates(tmp_path / "does-not-exist")
    assert cands == []


def test_reclaim_library_caches_dry_run_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "Caches"
    d = root / "Homebrew"
    d.mkdir(parents=True)
    (d / "blob").write_bytes(b"x" * 4096)
    monkeypatch.setattr(dar, "LIBRARY_CACHES_ROOT", root)
    freed = dar._reclaim_library_caches(dry_run=True)
    assert d.exists() and (d / "blob").exists(), "dry_run must NOT remove cache entries"
    assert freed >= 4096


def test_reclaim_library_caches_skips_permission_denied_without_raising(tmp_path, monkeypatch):
    root = tmp_path / "Caches"
    protected = root / "com.apple.protected"
    removable = root / "Homebrew"
    for d in (protected, removable):
        d.mkdir(parents=True)
        (d / "blob").write_bytes(b"x" * 1024)
    monkeypatch.setattr(dar, "LIBRARY_CACHES_ROOT", root)

    real_rmtree = dar.shutil.rmtree

    def fake_rmtree(path, *a, **k):
        if pathlib.Path(path) == protected:
            raise PermissionError("Operation not permitted")
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(dar.shutil, "rmtree", fake_rmtree)
    freed = dar._reclaim_library_caches(dry_run=False)  # must not raise
    assert protected.exists(), "permission-denied entry must be left in place"
    assert not removable.exists(), "a removable entry must still be reclaimed"
    assert freed >= 1024


def test_reclaim_library_caches_skips_nonempty_dir_race_without_raising(tmp_path, monkeypatch):
    # bus #73901 live incident: com.apple.python raised OSError(ENOTEMPTY) from rmtree
    # (a TOCTOU race -- something wrote into the cache dir mid-sweep), which was NOT a
    # PermissionError, so it fell through to the raise-RuntimeError branch and aborted the
    # whole reclaimer, triggering a false P1 on a HEALTHY disk. Library/Caches entries are
    # known-flaky (races, protected items) -- ANY OSError here must skip+continue, never raise.
    root = tmp_path / "Caches"
    racing = root / "com.apple.python"
    removable = root / "Homebrew"
    for d in (racing, removable):
        d.mkdir(parents=True)
        (d / "blob").write_bytes(b"x" * 1024)
    monkeypatch.setattr(dar, "LIBRARY_CACHES_ROOT", root)

    real_rmtree = dar.shutil.rmtree

    def fake_rmtree(path, *a, **k):
        if pathlib.Path(path) == racing:
            raise OSError(66, "Directory not empty")
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(dar.shutil, "rmtree", fake_rmtree)
    freed = dar._reclaim_library_caches(dry_run=False)  # must not raise
    assert racing.exists(), "the racing/non-empty entry must be left in place, not half-removed"
    assert not removable.exists(), "a removable entry must still be reclaimed"
    assert freed >= 1024
