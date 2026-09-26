"""agent_worktree_reaper — reap STALE Claude-Code-native agent worktrees (cc-fleet-health §4.6).

WHY: on 2026-09-27 the Mini DATA volume hit 89% and the real consumer was NOT caches — it was
`~/wingmen/projects/ihsanos/.claude/worktrees/` holding 145 stale Claude-native AGENT worktrees
(=20G), each a git worktree with its own node_modules, all >2 weeks abandoned. This is a distinct
disk-P1 source from the ~/wingmen/wt-* PR worktrees; the tiered disk_autoremediate does not look
inside project-local `.claude/worktrees/`. This closes that detect->act loop: find agent-* worktrees
that are OLD + CLEAN and remove them the git-native, reversible way (Nazim 43534: detect-only first,
armed after a review week).

DISCIPLINE (CLAUDE.md §2):
  - Verify before act: per worktree, read the ACTUAL `git status --porcelain` — a DIRTY tree
    (any modified OR untracked file) is NEVER reaped. Age is measured from the worktree mtime.
  - Reversible: `git worktree remove` WITHOUT --force keeps every branch + commit in the repo's
    object store — the worktree is recreatable; only uncommitted work would be lost, and the
    clean-gate (and git's own no-force refusal) protects that. NEVER --force, NEVER rm -rf
    (that strands git metadata). `git worktree prune` cleans admin files after.
  - Dead-man / fail-loud: a per-worktree removal failure is logged LOUDLY and recorded, never
    swallowed; removals are INDEPENDENT so one failure does not abort the sweep; --apply PAGES
    (bus, to orch-console) if any removal errored.
  - Staged arming: DETECT-ONLY by default (reports what it WOULD reap, touches nothing). --apply
    acts. Ships detect-only; armed to --apply only after Nazim reviews a week of detect reports.
  - Conservative gate: only `agent-*` worktrees (the CC agent-isolation scratch), only under a
    project's `.claude/worktrees/`. NAMED worktrees (e.g. tg-*) are left untouched and just listed.

Usage (from ~/wingmen/orchestrator):
  .venv/bin/python scripts/agent_worktree_reaper.py            # DETECT-ONLY (report only)
  .venv/bin/python scripts/agent_worktree_reaper.py --apply    # reap for real (ARMED)
  .venv/bin/python scripts/agent_worktree_reaper.py --json     # machine-readable report
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

GB = 1 << 30
MB = 1 << 20

# --- tunables (env-overridable) ---
MIN_AGE_DAYS = int(os.environ.get("AGENT_WT_MIN_AGE_DAYS", "14"))
MIN_AGE_S = MIN_AGE_DAYS * 86400
# Roots to scan; each root's immediate child dirs are candidate projects. Both cover the fleet
# layout (~/wingmen/<project> and ~/wingmen/projects/<project>); paths are de-duplicated.
SCAN_ROOTS = [pathlib.Path(os.path.expanduser(p)) for p in filter(None, os.environ.get(
    "AGENT_WT_SCAN_ROOTS", "~/wingmen:~/wingmen/projects").split(":"))]
GIT_TIMEOUT_S = int(os.environ.get("AGENT_WT_GIT_TIMEOUT_S", "60"))


def log(msg: str) -> None:
    print(msg, flush=True)


# ---- pure decision (fully unit-testable; no I/O) -------------------------------
def classify_worktree(*, has_git, mtime, now, min_age_s, porcelain, in_use):
    """Verdict for ONE agent worktree from primitives. Order matters: cheapest/safest gate first.
    - no-git: not a real worktree checkout (stray dir) — do NOT touch (unclear; report it).
    - recent: mtime within min_age → an active/recent session may resume it. SPARE.
    - dirty: `git status --porcelain` non-empty (modified OR untracked) → holds unsaved work. SPARE.
    - in-use: a process holds a file under it (kill-time protection). SPARE.
    - reap: old + clean + idle → safe, reversible git-native removal candidate."""
    if not has_git:
        return "skip:no-git"
    if now - mtime < min_age_s:
        return "skip:recent"
    if porcelain.strip():
        return "skip:dirty"
    if in_use:
        return "skip:in-use"
    return "reap"


# ---- fs / git probes (best-effort, fail-safe) ---------------------------------
def find_agent_worktrees(roots):
    """All `<root>/<project>/.claude/worktrees/agent-*` dirs, de-duplicated by real path.
    Best-effort: unreadable roots skipped, never crashes."""
    seen, out = set(), []
    for root in roots:
        try:
            projects = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for proj in projects:
            wt = proj / ".claude" / "worktrees"
            if not wt.is_dir():
                continue
            try:
                entries = sorted(wt.glob("agent-*"))
            except OSError:
                continue
            for d in entries:
                try:
                    rp = d.resolve()
                except OSError:
                    rp = d
                if d.is_dir() and rp not in seen:
                    seen.add(rp)
                    out.append(d)
    return out


def _has_git(path: pathlib.Path) -> bool:
    return (path / ".git").exists()


def _porcelain(path: pathlib.Path) -> str:
    """`git status --porcelain` for the worktree. Fail-SAFE: on any error return a sentinel
    non-empty string so the worktree classifies as dirty and is SPARED (never delete blind)."""
    try:
        r = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        if r.returncode != 0:
            return "?? _git_error_spare_me"
        return r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return "?? _git_error_spare_me"


def _last_commit_iso(path: pathlib.Path) -> str:
    """ISO date of the worktree's HEAD commit, or '' on error. Dir mtime only changes on
    top-level entry changes so it can OVERSTATE idleness (Nazim 43542); the last-commit date
    is a second, independent idleness signal reported alongside it for human judgement."""
    try:
        r = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%cI"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _lsof_in_use(path: pathlib.Path) -> bool:
    """True if any process holds a file under `path`. Fail-SAFE: on any lsof error treat as
    in-use (never remove something we cannot confirm is idle)."""
    try:
        r = subprocess.run(["lsof", "+D", str(path)], capture_output=True,
                           text=True, timeout=15)
        return bool(r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return True


def _dir_size(path: pathlib.Path) -> int:
    total = 0
    try:
        for dp, _dn, fn in os.walk(path):
            for f in fn:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _project_root(worktree: pathlib.Path) -> pathlib.Path:
    """agent-X/.claude/worktrees/agent-X -> the project's main checkout (3 levels up)."""
    return worktree.parent.parent.parent


def _git_remove(worktree: pathlib.Path) -> None:
    """git-native, NO --force (refuses a dirty/locked tree — belt-and-suspenders over our gate)."""
    repo = _project_root(worktree)
    r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(worktree)],
                       capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree remove refused: {r.stderr.strip() or r.stdout.strip()}")


# ---- sweep --------------------------------------------------------------------
def scan(roots, now, min_age_s):
    """Classify every agent worktree. Returns {reap:[...], skips:{reason:[...]}, total_reap_bytes}."""
    reap, skips = [], {}
    total = 0
    for wt in find_agent_worktrees(roots):
        has_git = _has_git(wt)
        try:
            mtime = wt.stat().st_mtime
        except OSError:
            mtime = now  # unreadable mtime -> treat as recent -> spare
        porcelain = _porcelain(wt) if has_git else ""
        # lsof is only consulted for otherwise-reapable candidates (it is the expensive probe)
        pre = classify_worktree(has_git=has_git, mtime=mtime, now=now, min_age_s=min_age_s,
                                porcelain=porcelain, in_use=False)
        verdict = pre
        if pre == "reap":
            verdict = classify_worktree(has_git=has_git, mtime=mtime, now=now, min_age_s=min_age_s,
                                        porcelain=porcelain, in_use=_lsof_in_use(wt))
        if verdict == "reap":
            sz = _dir_size(wt)
            total += sz
            reap.append({"path": str(wt), "bytes": sz,
                         "age_days": round((now - mtime) / 86400, 1),
                         "last_commit": _last_commit_iso(wt)})
        else:
            skips.setdefault(verdict, []).append(str(wt))
    return {"reap": reap, "skips": skips, "total_reap_bytes": total}


def apply_reap(reap):
    """Remove each candidate git-natively. Independent+additive: one failure is recorded and the
    sweep continues. Returns (removed[], errors[], repos_touched set)."""
    removed, errors, repos = [], [], set()
    for c in reap:
        wt = pathlib.Path(c["path"])
        try:
            _git_remove(wt)
            removed.append(c)
            repos.add(str(_project_root(wt)))
            log(f"  reaped {wt} (~{c['bytes'] // MB}MB, {c['age_days']}d)")
        except Exception as e:  # noqa: BLE001 — fail-LOUD, keep going
            errors.append({"path": c["path"], "error": repr(e)})
            log(f"  🔴 remove FAILED {wt}: {e!r} — recorded, continuing")
    for repo in sorted(repos):
        try:
            subprocess.run(["git", "-C", repo, "worktree", "prune"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError) as e:
            errors.append({"path": f"{repo} (prune)", "error": repr(e)})
            log(f"  🔴 prune FAILED {repo}: {e!r}")
    return removed, errors, repos


def _page(summary, removed, errors):
    lines = [
        "TL;DR: agent-worktree reaper hit removal error(s) — needs a look; nothing left half-done "
        "(removals are independent, dirty/locked worktrees were never touched).",
        f"reaped {len(removed)} worktree(s); {len(errors)} error(s).",
    ]
    for e in errors:
        lines.append(f"  🔴 {e['path']}: {e['error']}")
    banner = "🔴 AGENT-WORKTREE REAPER ERRORS\n  " + "\n  ".join(lines)
    print(banner, file=sys.stderr, flush=True)
    try:
        import psycopg2
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if dsn:
            c = psycopg2.connect(dsn)
            cur = c.cursor()
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
                           VALUES ('cc-fleet-health','orch-console','blocker','P1',
                           'agent-worktree reaper hit removal errors',%s)""",
                        ("\n".join(lines),))
            c.commit()
    except Exception as e:  # noqa: BLE001 — page best-effort; stderr banner already stands
        print(f"[agent-wt-reaper] WARN bus-page failed ({e}); banner stands", file=sys.stderr)


def _report(summary, mode, removed=None, errors=None):
    total_mb = summary["total_reap_bytes"] // MB
    log(f"[agent-wt-reaper {mode}] candidates={len(summary['reap'])} "
        f"would_free~{total_mb}MB ({summary['total_reap_bytes'] // GB}GB); "
        f"skips=" + ", ".join(f"{k}:{len(v)}" for k, v in sorted(summary["skips"].items())) or "none")
    for c in sorted(summary["reap"], key=lambda x: -x["bytes"])[:20]:
        log(f"    REAP {c['path']}  ~{c['bytes'] // MB}MB  mtime={c['age_days']}d  "
            f"last_commit={c.get('last_commit') or '?'}")
    # Named/unclear skips are surfaced so the owner can see what was deliberately left.
    for reason in ("skip:dirty", "skip:no-git"):
        for p in summary["skips"].get(reason, []):
            log(f"    LEFT ({reason}) {p}")
    if removed is not None:
        log(f"[agent-wt-reaper {mode}] removed={len(removed)} errors={len(errors or [])}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    as_json = "--json" in argv
    mode = "APPLIED" if apply else "DETECT-ONLY"
    now = time.time()
    summary = scan(SCAN_ROOTS, now, MIN_AGE_S)

    removed = errors = None
    if apply:
        removed, errors, _repos = apply_reap(summary["reap"])

    if as_json:
        print(json.dumps({"mode": mode, "min_age_days": MIN_AGE_DAYS,
                          "candidates": summary["reap"],
                          "skips": {k: len(v) for k, v in summary["skips"].items()},
                          "total_reap_bytes": summary["total_reap_bytes"],
                          "removed": removed, "errors": errors}, indent=2))
    else:
        _report(summary, mode, removed, errors)

    if apply and errors:
        _page(summary, removed, errors)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
