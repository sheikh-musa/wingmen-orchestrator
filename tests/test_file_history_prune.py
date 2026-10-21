"""Durable ~/.claude/file-history prune-monitor (disk-wedge recurrence fix, 2026-08-24).

A lane editing a GROWING file balloons ~/.claude/file-history/<session>/<hash>@vN (GBs per
edit) → wedges the whole disk (see [[disk-wedge-claude-file-history-runaway]]). This prunes
OLD snapshots of a BLOATED session (keeping the newest few — they're undo backups, safe) and
ALERTS before a wedge. plan_prune is the PURE decision (imported, not re-typed).

Run (from ~/wingmen/orchestrator): .venv/bin/python -m pytest tests/test_file_history_prune.py -q
"""
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
