"""Durable ~/.claude/file-history prune-monitor (disk-wedge recurrence fix, 2026-08-24).

A lane editing a GROWING file balloons ~/.claude/file-history/<session>/<hash>@vN (GBs per
edit) → wedges the whole disk (see [[disk-wedge-claude-file-history-runaway]]). This prunes
OLD snapshots of a BLOATED session (keeping the newest few — they're undo backups, safe) and
ALERTS before a wedge. plan_prune is the PURE decision (imported, not re-typed).

Run (from ~/wingmen/orchestrator): .venv/bin/python -m pytest tests/test_file_history_prune.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

from scripts import file_history_prune as fhp
from scripts.file_history_prune import alert_body, plan_prune

GB = 1 << 30


def _sess(sid, versions):
    return {"session": sid, "total": sum(v["size"] for v in versions), "versions": versions}


def _v(h, ver, size, mtime):
    return {"path": f"/fh/{h}@v{ver}", "hash": h, "size": size, "mtime": mtime}


def test_prunes_old_versions_of_a_bloated_session_keeping_newest():
    # 5 growing versions of one file, session well over the prune threshold -> keep newest 2.
    vs = [_v("fb", i, i * GB, mtime=i) for i in range(1, 6)]  # v1..v5, sizes 1..5 GB, v5 newest
    plan = plan_prune([_sess("s1", vs)], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB)
    assert set(plan["delete"]) == {"/fh/fb@v1", "/fh/fb@v2", "/fh/fb@v3"}  # v4,v5 kept (newest)
    assert plan["freed"] == (1 + 2 + 3) * GB


def test_leaves_small_sessions_undo_intact():
    # A session UNDER the prune threshold is untouched (don't nuke normal undo history).
    vs = [_v("x", i, 10 << 20, mtime=i) for i in range(1, 6)]  # ~50MB total
    plan = plan_prune([_sess("s2", vs)], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB)
    assert plan["delete"] == [] and plan["freed"] == 0


def test_alerts_when_a_session_exceeds_alert_cap():
    vs = [_v("fb", i, 4 * GB, mtime=i) for i in range(1, 4)]  # 12GB total
    plan = plan_prune([_sess("runaway", vs)], prune_over_bytes=2 * GB, keep=1, alert_over_bytes=10 * GB)
    assert any("runaway" in a for a in plan["alerts"])


# ── bus #73314: two-tier disk-free signal — this Mini's normal baseline is 9-11GB free, so ──
# ── a single old MIN_FREE=12GB alerted on EVERY run at a healthy level (alert fatigue, ~20 ──
# ── pages a session marked read unread). `alerts` (bus-paged) now needs genuine near-crash ──
# ── (< hard_free_bytes); `warnings` (local log only, never paged) covers the early signal. ──
def test_hard_floor_breach_pages_via_alerts():
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=3 * GB, warn_free_bytes=7 * GB, hard_free_bytes=6 * GB)
    assert any("disk free" in a.lower() for a in plan["alerts"])
    assert plan["warnings"] == []


def test_warn_floor_breach_is_local_only_not_paged():
    # 8GB free: below the 9GB warn floor, but still above the 6GB hard floor -- must warn
    # locally, and must NOT land in `alerts` (which is what triggers a bus page).
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=8 * GB, warn_free_bytes=9 * GB, hard_free_bytes=6 * GB)
    assert plan["alerts"] == []
    assert any("disk free" in w.lower() for w in plan["warnings"])


def test_healthy_disk_free_triggers_neither_tier():
    # This Mini's normal baseline (9-11GB) must not fire anything -- the whole point of #73314.
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=10 * GB, warn_free_bytes=7 * GB, hard_free_bytes=6 * GB)
    assert plan["alerts"] == []
    assert plan["warnings"] == []


# ── bus #73441: same pager-routing retune as disk_autoremediate.py PR#466 -- a hard-floor ──
# ── breach that's already diagnosed structural (no new action) must log, not wake, console. ──
def test_hard_floor_breach_in_managed_band_does_not_page_when_emergency_given():
    # 5GB: below hard floor (6GB) but at/above emergency floor (4GB) -- managed band.
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=5 * GB, warn_free_bytes=7 * GB, hard_free_bytes=6 * GB,
                      emergency_free_bytes=4 * GB)
    assert plan["alerts"] == [], "managed-band breach must NOT land in alerts (bus-paged, loud)"
    assert any("disk free" in m.lower() for m in plan["managed"])


def test_below_emergency_floor_still_pages_loud():
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=3 * GB, warn_free_bytes=7 * GB, hard_free_bytes=6 * GB,
                      emergency_free_bytes=4 * GB)
    assert any("disk free" in a.lower() for a in plan["alerts"])
    assert plan["managed"] == []


def test_omitting_emergency_floor_preserves_old_always_loud_behavior():
    # No emergency_free_bytes passed -> existing callers (and the old test above) are
    # unaffected: a hard-floor breach still pages loud, nothing goes to `managed`.
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=5 * GB, warn_free_bytes=7 * GB, hard_free_bytes=6 * GB)
    assert any("disk free" in a.lower() for a in plan["alerts"])
    assert plan["managed"] == []


def test_bloat_session_alert_is_never_downgraded_to_managed():
    # A genuinely bloated session is a DISTINCT finding -- never downgraded by the disk-free
    # band, even while free sits in the managed band.
    vs = [_v("fb", i, 4 * GB, mtime=i) for i in range(1, 4)]  # 12GB total, over alert cap
    plan = plan_prune([_sess("runaway", vs)], prune_over_bytes=2 * GB, keep=1,
                      alert_over_bytes=10 * GB, disk_free_bytes=5 * GB, warn_free_bytes=7 * GB,
                      hard_free_bytes=6 * GB, emergency_free_bytes=4 * GB)
    assert any("runaway" in a for a in plan["alerts"])


def test_keep_boundary_exact_versions_kept():
    vs = [_v("fb", i, 3 * GB, mtime=i) for i in range(1, 3)]  # exactly 2 versions
    plan = plan_prune([_sess("s3", vs)], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=100 * GB)
    assert plan["delete"] == []  # keep==count -> nothing deleted


# ── bus #73188: the bus-page body must not claim file-history is the driver unless a ──
# ── session actually crossed the bloat/alert cap (verify-not-assert, not an unconditional claim) ──
def test_alert_body_does_not_blame_file_history_on_generic_low_disk():
    plan = plan_prune([], prune_over_bytes=2 * GB, keep=2, alert_over_bytes=10 * GB,
                      disk_free_bytes=3 * GB, warn_free_bytes=7 * GB, hard_free_bytes=6 * GB)
    body = alert_body(plan["alerts"])
    assert "NOT the driver" in body
    assert "snapshotting a large growing file" not in body


def test_alert_body_blames_file_history_when_a_session_actually_is_bloated():
    vs = [_v("fb", i, 4 * GB, mtime=i) for i in range(1, 4)]  # 12GB total
    plan = plan_prune([_sess("runaway", vs)], prune_over_bytes=2 * GB, keep=1, alert_over_bytes=10 * GB)
    body = alert_body(plan["alerts"])
    assert "snapshotting a large growing file" in body
    assert "NOT the driver" not in body


def test_prune_is_per_hash_within_a_session():
    # Two different files in one bloated session -> keep newest `keep` of EACH, not globally.
    vs = ([_v("a", i, 2 * GB, mtime=i) for i in range(1, 4)] +
          [_v("b", i, 2 * GB, mtime=i) for i in range(1, 4)])
    plan = plan_prune([_sess("s4", vs)], prune_over_bytes=2 * GB, keep=1, alert_over_bytes=100 * GB)
    # keep newest (v3) of each hash -> delete a@v1,a@v2,b@v1,b@v2
    assert set(plan["delete"]) == {"/fh/a@v1", "/fh/a@v2", "/fh/b@v1", "/fh/b@v2"}


# ── bus #73447: managed band must be LOG-ONLY -- the console doorbell rings on any new ──
# ── unread row regardless of priority, so even a P3 row was still indefinite churn. ──
def test_alert_loud_false_never_attempts_db_write(monkeypatch, capsys):
    import psycopg2
    def _boom(*a, **k):
        raise AssertionError("loud=False must never attempt a DB connection")
    monkeypatch.setattr(psycopg2, "connect", _boom)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    fhp._alert(["disk free 5GB < hard floor 6GB — pruning file-history (disk-wedge risk)"],
              loud=False)
    err = capsys.readouterr().err
    assert "log-only, no bus row" in err


def test_alert_loud_true_still_attempts_db_write(monkeypatch):
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
    fhp._alert(["disk free 3GB < hard floor 6GB — pruning file-history + escalate"], loud=True)
    assert calls["connect"] == 1, "loud=True must still attempt the bus row"


def test_alert_survives_bus_send_raising_system_exit(monkeypatch, capsys):
    """Musa op#34845, bus #78369 finding E: bus_send.send() can raise bare SystemExit
    (e.g. dburl() when no DATABASE_URL resolves -- bus_send.py:533), a BaseException a
    plain `except Exception` does not catch -- would crash the whole prune instead of
    logging a WARN, contradicting _alert's own docstring ('a delivery failure must not
    crash the prune')."""
    from scripts import bus_send
    def _raise_system_exit(*a, **k):
        raise SystemExit("no DATABASE_URL: set it in the environment or .env")
    monkeypatch.setattr(bus_send, "send", _raise_system_exit)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    fhp._alert(["disk free 3GB < hard floor 6GB — pruning file-history + escalate"], loud=True)
    err = capsys.readouterr().err
    assert "WARN bus-page failed" in err


def test_bus_page_import_resolves_under_real_launchd_invocation(tmp_path):
    """Musa op#34845, bus #78369 finding C (verified): boot_file_history_prune.sh runs this
    file by its plain absolute path (`exec .venv/bin/python3 $ORCH_DIR/scripts/file_history_
    prune.py --apply`), no PYTHONPATH -- sys.path[0] under that invocation is scripts/ (this
    file's own directory), so `from scripts import bus_send` (the only import attempt, no
    fallback) raises ModuleNotFoundError, caught by _alert's own `except Exception` and
    logged as a bare WARN -- the stderr banner for the real P1 disk-wedge-risk condition
    still prints (it fires before the try block), but the bus page to orch-console that is
    supposed to wake someone up is silently lost every time this runs for real. pytest never
    sees this because pytest.ini's `pythonpath = .` makes `scripts` importable regardless.

    Forces the real alert path deterministically without touching any real file-history data
    or real disk state: an empty CLAUDE_FILE_HISTORY_DIR (no sessions, nothing to prune) plus
    FH_HARD_FREE_GB/FH_EMERGENCY_FREE_GB set far above any real free-disk figure, so this
    machine's actual free space is always "below" both thresholds and plan_prune's genuine
    disk-free alert fires loud=True through the real CLI (main() -> plan_prune -> _alert),
    exactly the path the launchd job takes."""
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "file_history_prune.py"
    empty_fh_dir = tmp_path / "file-history-empty"
    empty_fh_dir.mkdir()
    clean_env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "CLAUDE_FILE_HISTORY_DIR": str(empty_fh_dir),
        "FH_HARD_FREE_GB": "999999999",
        "FH_EMERGENCY_FREE_GB": "999999999",
        "DATABASE_URL": "x",  # truthy, non-DSN-shaped -- just needs to clear `if dsn:`
    }
    result = subprocess.run(
        [sys.executable, str(script), "--apply"],
        cwd="/tmp", env=clean_env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "DISK-WEDGE RISK" in result.stderr, (
        f"the forced disk-free condition never reached the loud alert path -- test setup is "
        f"wrong, this isn't proving anything: {result.stderr}"
    )
    assert "ModuleNotFoundError" not in result.stderr and "No module named" not in result.stderr, (
        f"the bus page's own import failed and was silently swallowed as a WARN -- "
        f"orch-console never gets paged for a real disk-wedge-risk event:\n{result.stderr}"
    )
