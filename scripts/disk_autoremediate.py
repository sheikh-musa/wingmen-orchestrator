"""disk_autoremediate — tiered PRE-CRASH disk reclaim for the Mac Mini (cc-fleet-health §4.6).

WHY: on 2026-08-28 the fleet took a ~7h outage — the DATA volume free hit ~0, and every
tmux lane (workers + singletons) crashed on [Errno 28] No space left; they're tmux/boot-script
(NOT launchd) so nothing auto-restarted. The disk monitor (disk_space_monitor.sh, console-scoped)
ALERTED but AUTO-REMEDIATED NOTHING — it only paged. This closes that detect->act loop: when
free crosses a SAFE threshold (well before the crash zone) it runs the REVERSIBLE reclaimers the
SRE already owns, stopping as soon as it's back in the safe range, and PAGES loud only if free
stays below a hard floor after reclaim (or a reclaimer errors). It NEVER touches non-regenerable
data or operator-held paths.

DISCIPLINE (CLAUDE.md §2):
  - Reversible: every reclaimer target regenerates (npm/brew caches, .next/cache, file-history
    undo snapshots). Never a real work file.
  - Dead-man / fail-loud: a reclaimer failure is logged LOUDLY and recorded, never silently
    swallowed; if free stays below the floor OR any reclaimer errored, it PAGES. Reclaimers are
    INDEPENDENT + ADDITIVE, so a single failure does NOT abort the sweep (a disk emergency wants
    max reclaim) — this is the considered deviation from blanket-abort, safe because no reclaimer
    depends on another's completion and none leaves a half-written state.
  - Staged arming: DRY-RUN by default (reports what it WOULD free, touches nothing). --apply acts.
    Ships dry-run; armed to --apply only after operator/Nazim reviews the dry-run output.
  - Held paths (EXCLUDE_PROJECTS): the cosem-video ~1GB cache is operator-held (decommission
    reclaims it) — never auto-reaped here.

Usage (from ~/wingmen/orchestrator):
  .venv/bin/python scripts/disk_autoremediate.py            # DRY-RUN (report only)
  .venv/bin/python scripts/disk_autoremediate.py --apply    # reclaim for real (ARMED)
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

GB = 1 << 30

# --- tunables (env-overridable) ---
DATA_VOL = os.environ.get("DISK_DATA_VOL", "/System/Volumes/Data")
REMEDIATE_UNDER = int(os.environ.get("DISK_REMEDIATE_UNDER_GB", "20")) * GB  # reclaim below this
FLOOR = int(os.environ.get("DISK_FLOOR_GB", "8")) * GB                       # page if still below
PROJECTS_ROOT = pathlib.Path(os.environ.get("WINGMEN_PROJECTS_ROOT",
                                            os.path.expanduser("~/wingmen/projects")))
NEXT_CACHE_MIN_AGE_S = int(os.environ.get("DISK_NEXT_CACHE_MIN_AGE_S", "3600"))  # >60min
# operator-held / never-auto-reaped project dirs (first path component under PROJECTS_ROOT)
EXCLUDE_PROJECTS = set(filter(None, os.environ.get(
    "DISK_EXCLUDE_PROJECTS", "cosem-video-pipeline").split(",")))


def log(msg: str) -> None:
    print(msg, flush=True)


# ---- pure decision -------------------------------------------------------------
def decide(free, *, remediate_under, floor):
    """free AT or below `remediate_under` triggers reclaim (inclusive); strictly above is ok."""
    return "remediate" if free <= remediate_under else "ok"


def _disk_free(path=DATA_VOL):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


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


# ---- .next/cache candidate scan (pure-ish; fs read only) -----------------------
def next_cache_candidates(root, exclude, older_than_s, now):
    """Return `.next/cache` dirs under root/<project>/** that are (a) NOT in an excluded
    project and (b) older than `older_than_s` (mtime) — an active build touches its cache,
    so a fresh mtime spares it. Best-effort: unreadable entries skipped, never crashes."""
    root = pathlib.Path(root)
    out = []
    try:
        walker = os.walk(root)
    except OSError:
        return out
    for dp, dns, _fn in walker:
        p = pathlib.Path(dp)
        if p.name == "cache" and p.parent.name == ".next":
            try:
                project = p.relative_to(root).parts[0]
            except (ValueError, IndexError):
                continue
            if project in exclude:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if now - mtime >= older_than_s:
                out.append(p)
    return out


def _lsof_in_use(path) -> bool:
    """True if any process holds a file under `path` (kill-time protection). Best-effort:
    on any lsof error, FAIL SAFE = treat as in-use (never delete something we can't clear)."""
    try:
        r = subprocess.run(["lsof", "+D", str(path)], capture_output=True,
                           text=True, timeout=15)
        return bool(r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return True


# ---- reclaimer sweep -----------------------------------------------------------
def run_reclaimers(reclaimers, free_probe, target, dry_run):
    """Run reclaimers IN ORDER, re-probing actual disk free after each; STOP once free >= target
    (don't over-reclaim). Each reclaimer is isolated: an exception is recorded in `errors` and the
    sweep CONTINUES (independent + additive). Returns a summary dict."""
    results, errors = [], []
    stopped_early = False
    free_before = free_probe()  # baseline (for logging); early-stop is checked AFTER each apply
    if free_before is not None:
        log(f"  baseline free ~{free_before // GB}GB; target ~{target // GB}GB")
    for rec in reclaimers:
        name = rec["name"]
        try:
            freed = rec["apply"](dry_run)
            results.append({"name": name, "freed": freed or 0})
            log(f"  reclaimer {name}: {'would free' if dry_run else 'freed'} "
                f"~{(freed or 0) // GB}GB ({(freed or 0) // (1<<20)}MB)")
        except Exception as e:  # noqa: BLE001 — fail-LOUD but keep reclaiming (disk emergency)
            errors.append({"name": name, "error": repr(e)})
            log(f"  🔴 reclaimer {name} FAILED: {e!r} — recorded, continuing")
        free_now = free_probe()
        if free_now is not None and free_now >= target:
            stopped_early = True
            log(f"  target met (free ~{free_now // GB}GB >= {target // GB}GB) — stopping early")
            break
    return {"results": results, "errors": errors, "stopped_early": stopped_early}


# ---- concrete reclaimers -------------------------------------------------------
def _reclaim_file_history(dry_run):
    """Reuse the owned file_history_prune (the #1 known runaway). Returns freed bytes."""
    try:
        from scripts import file_history_prune as fhp  # test / package-context import
    except ImportError:
        import file_history_prune as fhp               # run-as-script (scripts/ on sys.path[0])
    sessions = fhp.scan()
    plan = fhp.plan_prune(sessions, prune_over_bytes=fhp.PRUNE_OVER, keep=fhp.KEEP,
                          alert_over_bytes=fhp.ALERT_OVER)
    _deleted, freed = fhp.apply_prune(plan["delete"], dry_run=dry_run)
    return freed


def _reclaim_dir_cmd(size_dir, cmd):
    """Generic cache reclaimer: dry-run reports the dir's current size (would-free);
    apply runs `cmd` and returns the size delta (best-effort)."""
    def _apply(dry_run):
        before = _dir_size(pathlib.Path(size_dir)) if size_dir else 0
        if dry_run:
            return before
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"{cmd[0]} failed: {e}") from e
        after = _dir_size(pathlib.Path(size_dir)) if size_dir else 0
        return max(0, before - after)
    return _apply


def _reclaim_next_cache(dry_run):
    import time
    cands = next_cache_candidates(PROJECTS_ROOT, EXCLUDE_PROJECTS,
                                  NEXT_CACHE_MIN_AGE_S, time.time())
    freed = 0
    for d in cands:
        sz = _dir_size(d)
        if dry_run:
            freed += sz
            continue
        if _lsof_in_use(d):  # KILL-TIME re-verify — skip if a build is using it
            log(f"    .next/cache {d} in use (lsof) — skipped")
            continue
        try:
            shutil.rmtree(d)
            freed += sz
        except OSError as e:
            raise RuntimeError(f"rmtree {d} failed: {e}") from e
    return freed


def build_reclaimers():
    """Ordered safest/highest-known-yield first."""
    brew_cache = None
    try:
        r = subprocess.run(["brew", "--cache"], capture_output=True, text=True, timeout=15)
        brew_cache = r.stdout.strip() or None
    except (subprocess.TimeoutExpired, OSError):
        brew_cache = None
    return [
        {"name": "file-history", "apply": _reclaim_file_history},
        {"name": "npm-cache", "apply": _reclaim_dir_cmd(
            os.path.expanduser("~/.npm"), ["npm", "cache", "clean", "--force"])},
        {"name": "brew-cleanup", "apply": _reclaim_dir_cmd(
            brew_cache, ["brew", "cleanup", "-s"])},
        {"name": "next-cache", "apply": _reclaim_next_cache},
    ]


# ---- fail-loud page ------------------------------------------------------------
def _page(free_before, free_after, summary):
    lines = [
        f"TL;DR: disk auto-remediation ran but free is STILL below the {FLOOR // GB}GB floor "
        f"(or a reclaimer errored) — needs a look before the fleet crashes on [Errno 28].",
        f"free before={free_before // GB if free_before else '?'}GB "
        f"after={free_after // GB if free_after else '?'}GB (floor {FLOOR // GB}GB).",
    ]
    for r in summary["results"]:
        lines.append(f"  reclaimed {r['name']}: ~{r['freed'] // GB}GB")
    for e in summary["errors"]:
        lines.append(f"  🔴 {e['name']} FAILED: {e['error']}")
    banner = "🔴 DISK AUTO-REMEDIATION INSUFFICIENT\n  " + "\n  ".join(lines)
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
                           'disk auto-remediation insufficient — free still below floor',%s)""",
                        ("\n".join(lines),))
            c.commit()
    except Exception as e:  # noqa: BLE001 — page best-effort; stderr banner already stands
        print(f"[disk-autoremediate] WARN bus-page failed ({e}); banner stands", file=sys.stderr)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    mode = "APPLIED" if apply else "DRY-RUN"
    free = _disk_free()
    if free is None:
        print(f"[disk-autoremediate] ERROR could not read {DATA_VOL} free", file=sys.stderr)
        return 1
    tier = decide(free, remediate_under=REMEDIATE_UNDER, floor=FLOOR)
    log(f"[disk-autoremediate {mode}] free={free // GB}GB tier={tier} "
        f"(remediate<{REMEDIATE_UNDER // GB}GB floor={FLOOR // GB}GB)")
    if tier == "ok":
        return 0
    summary = run_reclaimers(build_reclaimers(), free_probe=_disk_free,
                             target=REMEDIATE_UNDER, dry_run=not apply)
    free_after = _disk_free()
    total_freed = sum(r["freed"] for r in summary["results"])
    log(f"[disk-autoremediate {mode}] {'would free' if not apply else 'freed'} "
        f"~{total_freed // GB}GB total; free after={free_after // GB if free_after else '?'}GB; "
        f"errors={len(summary['errors'])}")
    # Fail-loud PAGE (apply mode only): still below floor after reclaim, or any reclaimer errored.
    if apply and ((free_after is not None and free_after < FLOOR) or summary["errors"]):
        _page(free, free_after, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
