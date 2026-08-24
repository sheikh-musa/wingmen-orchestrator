"""Durable ~/.claude/file-history prune-monitor — disk-wedge recurrence fix (2026-08-24).

WHY: a LIVE lane editing a GROWING file balloons `~/.claude/file-history/<session>/<hash>@vN`
— Claude Code snapshots the WHOLE file per Edit/Write, GBs per edit — and can wedge the
entire local disk to 100% (fleet-wide freeze; see the 2026-08-24 incident,
[[disk-wedge-claude-file-history-runaway]]). At 100% NO agent can self-recover (the harness
can't even stage its own task-output file). So this runs BEFORE the wedge: it prunes OLD
snapshots of a BLOATED session (keeping the newest few — they are UNDO BACKUPS, never the
work files) and ALERTS the console/operator early.

SAFETY: only touches `~/.claude/file-history/**` (undo backups). NEVER deletes a real edited
file. Only prunes sessions OVER a size threshold (normal small sessions' undo stays intact).
Keeps the newest `keep` versions per (session, file-hash). Fail-loud: any prune error is
logged loudly and the run aborts that session, never silently swallowed.

Usage (from ~/wingmen/orchestrator):
  .venv/bin/python scripts/file_history_prune.py            # DRY-RUN (report only)
  .venv/bin/python scripts/file_history_prune.py --apply    # prune for real
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

GB = 1 << 30

# Tunables (env-overridable; conservative — only bloated sessions are touched).
FH_DIR = pathlib.Path(os.environ.get("CLAUDE_FILE_HISTORY_DIR",
                                     os.path.expanduser("~/.claude/file-history")))
PRUNE_OVER = int(os.environ.get("FH_PRUNE_OVER_GB", "3")) * GB   # only prune a session this big+
KEEP = int(os.environ.get("FH_KEEP_VERSIONS", "2"))             # newest N versions kept per file
ALERT_OVER = int(os.environ.get("FH_ALERT_OVER_GB", "8")) * GB   # a session this big pages early
MIN_FREE = int(os.environ.get("FH_MIN_FREE_GB", "12")) * GB      # disk-free floor that pages


def _ver_num(path: str) -> int:
    """The @vN number for stable ordering when mtimes tie (they do under one tx)."""
    tail = path.rsplit("@v", 1)
    try:
        return int(tail[1]) if len(tail) == 2 else 0
    except ValueError:
        return 0


def plan_prune(sessions, *, prune_over_bytes, keep, alert_over_bytes,
               disk_free_bytes=None, min_free_bytes=None):
    """PURE decision. sessions: [{'session', 'total', 'versions':[{'path','hash','size','mtime'}]}].
    Returns {'delete':[paths], 'freed':bytes, 'alerts':[str]}. Prunes ONLY sessions at/over
    prune_over_bytes; within those, keeps the newest `keep` versions per file-hash (newest by
    mtime, then @vN) and marks the rest to delete. Alerts on a session at/over alert_over_bytes
    or disk free below min_free_bytes."""
    delete, freed, alerts = [], 0, []
    for s in sessions:
        total = s.get("total", 0)
        if total >= alert_over_bytes:
            alerts.append(f"file-history session {s['session']} at {total // GB}GB "
                          f"(>= {alert_over_bytes // GB}GB alert cap) — a lane is snapshotting a large growing file")
        if total < prune_over_bytes:
            continue  # leave normal-sized sessions' undo history intact
        by_hash: dict = {}
        for v in s.get("versions", []):
            by_hash.setdefault(v["hash"], []).append(v)
        for _hash, vs in by_hash.items():
            vs_sorted = sorted(vs, key=lambda v: (v["mtime"], _ver_num(v["path"])), reverse=True)
            for v in vs_sorted[keep:]:
                delete.append(v["path"])
                freed += v["size"]
    if disk_free_bytes is not None and min_free_bytes is not None and disk_free_bytes < min_free_bytes:
        alerts.append(f"disk free {disk_free_bytes // GB}GB < min {min_free_bytes // GB}GB "
                      f"— pruning file-history + escalate (disk-wedge risk)")
    return {"delete": delete, "freed": freed, "alerts": alerts}


def scan(fh_dir: pathlib.Path = FH_DIR):
    """Walk file-history into the plan_prune shape. Best-effort: unreadable entries are skipped
    (never crash the monitor). A version file is `<session>/<hash>@vN`."""
    sessions = []
    try:
        session_dirs = [d for d in fh_dir.iterdir() if d.is_dir()]
    except OSError:
        return sessions
    for sd in session_dirs:
        versions, total = [], 0
        try:
            entries = list(sd.iterdir())
        except OSError:
            continue
        for f in entries:
            if not f.is_file() or "@v" not in f.name:
                continue
            try:
                size = f.stat().st_size
                mtime = f.stat().st_mtime
            except OSError:
                continue
            versions.append({"path": str(f), "hash": f.name.rsplit("@v", 1)[0],
                             "size": size, "mtime": mtime})
            total += size
        if versions:
            sessions.append({"session": sd.name, "total": total, "versions": versions})
    return sessions


def apply_prune(delete, *, dry_run=True):
    """Delete the marked version files. Fail-LOUD: an unexpected error re-raises so a broken
    prune is never silent. Returns (deleted_count, freed_bytes)."""
    deleted, freed = 0, 0
    for p in delete:
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue  # already gone
        if dry_run:
            deleted += 1
            freed += sz
            continue
        try:
            os.remove(p)
            deleted += 1
            freed += sz
        except IsADirectoryError:
            shutil.rmtree(p, ignore_errors=False)
            deleted += 1
            freed += sz
        except FileNotFoundError:
            continue  # raced away — fine
    return deleted, freed


def _disk_free(path="/System/Volumes/Data"):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


def _alert(msgs):
    """Fail-loud alert. Prints a LOUD banner to stderr (lands in the launchd log). The bus/
    operator page is best-effort (and this monitor runs BEFORE a wedge, so the bus is alive);
    a delivery failure must not crash the prune."""
    banner = "🔴 FILE-HISTORY DISK-WEDGE RISK\n  " + "\n  ".join(msgs)
    print(banner, file=sys.stderr, flush=True)
    try:
        import psycopg2
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if dsn:
            c = psycopg2.connect(dsn)
            cur = c.cursor()
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            body = ("TL;DR: ~/.claude/file-history is ballooning (a lane snapshotting a large growing "
                    "file) — disk-wedge risk. Pruned old snapshots; details:\n  " + "\n  ".join(msgs))
            cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
                           VALUES ('cc-fleet-health','orch-console','blocker','P1',
                           'file-history disk-wedge risk — pruned + flagging',%s)""", (body,))
            c.commit()
    except Exception as e:  # noqa: BLE001 — page is best-effort; the stderr banner already fired
        print(f"[file-history-prune] WARN bus-page failed ({e}); stderr banner stands", file=sys.stderr)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    sessions = scan()
    free = _disk_free()
    plan = plan_prune(sessions, prune_over_bytes=PRUNE_OVER, keep=KEEP,
                      alert_over_bytes=ALERT_OVER, disk_free_bytes=free, min_free_bytes=MIN_FREE)
    deleted, freed = apply_prune(plan["delete"], dry_run=not apply)
    total_fh = sum(s["total"] for s in sessions)
    mode = "APPLIED" if apply else "DRY-RUN"
    print(f"[file-history-prune {mode}] sessions={len(sessions)} total={total_fh // GB}GB "
          f"free={(free // GB) if free else '?'}GB pruned={deleted} freed={freed // GB}GB "
          f"alerts={len(plan['alerts'])}", flush=True)
    if plan["alerts"] and apply:
        _alert(plan["alerts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
