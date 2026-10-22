"""Durable ~/.claude/file-history prune-monitor (disk-wedge recurrence fix, 2026-08-24).

A lane editing a GROWING file balloons ~/.claude/file-history/<session>/<hash>@vN (GBs per
edit) → wedges the whole disk (see [[disk-wedge-claude-file-history-runaway]]). This prunes
OLD snapshots of a BLOATED session (keeping the newest few — they're undo backups, safe) and
ALERTS before a wedge. plan_prune is the PURE decision (imported, not re-typed).

Run (from ~/wingmen/orchestrator): .venv/bin/python -m pytest tests/test_file_history_prune.py -q
"""
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
