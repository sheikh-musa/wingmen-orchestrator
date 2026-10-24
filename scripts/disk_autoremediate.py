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
  - Reversible: every reclaimer target regenerates (npm/brew caches, .next build output, file-history
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

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

GB = 1 << 30

# --- tunables (env-overridable) ---
DATA_VOL = os.environ.get("DISK_DATA_VOL", "/System/Volumes/Data")
REMEDIATE_UNDER = int(os.environ.get("DISK_REMEDIATE_UNDER_GB", "20")) * GB  # reclaim below this
# 2026-10-22 retune (bus #73391): steady-state free sits at ~7-9GB once the fleet's live
# lanes are up — this floor used to page P1 on EVERY tick once free dipped under it, even
# though the real crash zone is ~0GB (2026-08-28 [Errno 28] incident) and reclaim was
# already exhausted (0GB freed per tick). Same lesson + same number as the HARD_FREE_GB
# floor in file_history_prune.py (PR#463) and disk_space_monitor.sh (PR#464): 6GB is the
# genuine near-crash margin, not 8GB which is this Mini's normal healthy baseline.
FLOOR = int(os.environ.get("DISK_FLOOR_GB", "6")) * GB                       # page if still below
# 2026-10-22 pager-routing retune (bus #73430): steady-state has fallen further and can now
# sit genuinely below FLOOR with 0GB reclaimable (structural capacity+load per orch-console's
# own du/lsof dig, not a leak) — a REAL floor breach, but with NO new action, since the
# external-drive fix is already escalated to Musa and both sides hold the emergency line below.
# Paging P1 to console every ~5min for an already-handled condition is the exact
# perpetual-noise pattern the FLOOR retune (PR#465) just killed, reappearing one tier down.
# So: FLOOR..EMERGENCY_FLOOR is a "managed band" — log-only / P3, no console wake. Below
# EMERGENCY_FLOOR (or any reclaimer error) is the genuine near-[Errno 28] emergency and still
# pages loud (P1) exactly as before.
EMERGENCY_FLOOR = int(os.environ.get("DISK_EMERGENCY_FREE_GB", "4")) * GB     # always page LOUD
PROJECTS_ROOT = pathlib.Path(os.environ.get("WINGMEN_PROJECTS_ROOT",
                                            os.path.expanduser("~/wingmen/projects")))
NEXT_CACHE_MIN_AGE_S = int(os.environ.get("DISK_NEXT_CACHE_MIN_AGE_S", "3600"))  # >60min
# 2026-10-22 (bus #73708): orch-console hand-reclaimed these during the disk-crash-averted
# fix and asked for them as standing reclaimer tiers. BACKUPS_ROOT holds date-named daily
# snapshot dirs (sortable lexicographically); LIBRARY_CACHES_ROOT is the per-user macOS
# cache dir (Apple-blessed as regenerable, distinct from app support/profile data).
BACKUPS_ROOT = pathlib.Path(os.environ.get("WINGMEN_BACKUPS_ROOT",
                                           os.path.expanduser("~/wingmen/backups")))
BACKUP_KEEP_DAYS = int(os.environ.get("DISK_BACKUP_KEEP_DAYS", "3"))
LIBRARY_CACHES_ROOT = pathlib.Path(os.environ.get("DISK_LIBRARY_CACHES_ROOT",
                                                  os.path.expanduser("~/Library/Caches")))
# operator-held / never-auto-reaped project dirs (first path component under PROJECTS_ROOT)
EXCLUDE_PROJECTS = set(filter(None, os.environ.get(
    "DISK_EXCLUDE_PROJECTS", "cosem-video-pipeline").split(",")))


def log(msg: str) -> None:
    print(msg, flush=True)


# ---- pure decision -------------------------------------------------------------
def decide(free, *, remediate_under, floor):
    """free AT or below `remediate_under` triggers reclaim (inclusive); strictly above is ok."""
    return "remediate" if free <= remediate_under else "ok"


def page_severity(free_after, errors, *, floor, emergency_floor):
    """How loud to page after a reclaim attempt (bus #73430 pager-routing retune;
    RETUNED bus #73901 to decouple reclaimer errors from disk danger).

    An unreadable free-probe is always 'loud' — can't confirm safety. Below
    `emergency_floor` is the genuine near-crash zone — always 'loud'. A reclaimer ERROR
    combined with an actual floor breach is still the real emergency — 'loud'. But a
    reclaimer error on an otherwise-HEALTHY disk (free_after >= floor) is a tooling bug,
    not a disk emergency — 'log' only, same as the managed band, never a false P1.
    Between `emergency_floor` and `floor` with no errors: the MANAGED band, already
    diagnosed as structural with no new action — 'log' only, no console wake. At/above
    `floor` with no errors: 'none'.
    """
    if free_after is None:
        return "loud"
    if free_after < emergency_floor:
        return "loud"
    if errors and free_after < floor:
        return "loud"
    if free_after < floor:
        return "log"
    if errors:
        return "log"
    return "none"


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


# ---- .next candidate scan (pure-ish; fs read only) -----------------------------
def next_cache_candidates(root, exclude, older_than_s, now):
    """Return whole `.next` build-output dirs under root/<project>/** that are (a) NOT in an
    excluded project and (b) older than `older_than_s` (mtime) — an active build touches its
    cache, so a fresh mtime spares it. Best-effort: unreadable entries skipped, never crashes.

    WIDENED (orch-console bus #76559, 2026-10-26): used to take only the nested `.next/cache`
    subdir, missing the rest of `.next` (static/server compiled output) -- the bulk of what
    orch-console hand-reclaimed (3.6GB) during the 2026-10-26 disk emergency this reclaimer
    should have caught on its own. The WHOLE `.next` dir is equally regenerable (`next build`
    recreates it start to finish), so take it all, not just the cache subdir. Pruning the walk
    (`dns[:] = []`) once matched avoids descending into a dir we're already taking wholesale."""
    root = pathlib.Path(root)
    out = []
    try:
        walker = os.walk(root)
    except OSError:
        return out
    for dp, dns, _fn in walker:
        p = pathlib.Path(dp)
        if p.name == ".next":
            dns[:] = []  # don't descend into a dir we're taking wholesale
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


def _reclaim_dir_cmd(size_dir, cmd, *, measure_via_disk_free=False):
    """Generic cache reclaimer: dry-run reports the dir's current size (would-free);
    apply runs `cmd` and returns the size delta (best-effort). Set `measure_via_disk_free`
    when the command's real effect isn't confined to `size_dir` — e.g. `brew cleanup -s`
    also prunes OLD Cellar formula versions, not just the download cache, so measuring only
    `size_dir` (the cache) silently reports ~0GB freed even when real disk was reclaimed
    (bus #73431: a manual `brew cleanup -s` freed ~1GB that the automated reclaimer's own
    per-tick log kept showing as 0GB — misleading attribution, not a missing/soft command;
    the hard variant was already in use). Dry-run is unaffected (no side effect to measure a
    delta around, so it still uses the cheap dir-size estimate)."""
    def _apply(dry_run):
        before_dir = _dir_size(pathlib.Path(size_dir)) if size_dir else 0
        if dry_run:
            return before_dir
        before_disk = _disk_free() if measure_via_disk_free else None
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           env=_augmented_env())
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"{cmd[0]} failed: {e}") from e
        if measure_via_disk_free and before_disk is not None:
            after_disk = _disk_free()
            if after_disk is not None:
                return max(0, after_disk - before_disk)
        after_dir = _dir_size(pathlib.Path(size_dir)) if size_dir else 0
        return max(0, before_dir - after_dir)
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
            log(f"    .next {d} in use (lsof) — skipped")
            continue
        try:
            shutil.rmtree(d)
            freed += sz
        except OSError as e:
            raise RuntimeError(f"rmtree {d} failed: {e}") from e
    return freed


# Where CLI tools live when PATH is minimal. launchd/agent context does NOT inherit
# the login PATH, so bare `["npm", ...]` / `["brew", ...]` raised FileNotFoundError and
# the critical-tier reclaimers silently no-op'd (bus #44270). Resolve an absolute path.
_BIN_DIRS = ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]


def _resolve_bin(name):
    """Absolute path to `name`, robust to a minimal launchd PATH: honour the current
    PATH first (which()), then fall back to the known bin dirs. None if truly absent."""
    found = shutil.which(name)
    if found:
        return found
    for d in _BIN_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _augmented_env():
    """os.environ with the known bin dirs PREPENDED to PATH. Resolving a tool to an
    absolute path is not enough when the tool is a shebang script: /usr/local/bin/npm
    starts with `#!/usr/bin/env node`, and that `env node` re-resolves node via the
    CHILD's PATH — which under launchd is minimal, so it fails "node: No such file or
    directory" (bus #44288). Handing the subprocess an augmented PATH lets the shebang
    find node/its helpers even when we launched it by absolute path."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(_BIN_DIRS) + os.pathsep + env.get("PATH", "")
    return env


# ---- Chrome cache reclaimer -----------------------------------------------------
# Operator-authorized 2026-10-14 (Musa: "go ahead ... add it to the list of clearable
# things in future so you never have to ask"). ALL targets regenerate: HTTP/code/GPU/
# shader caches, Service Worker CacheStorage/ScriptCache, and OptGuideOnDeviceModel
# (Chrome re-downloads the on-device model on demand). NEVER touches profile/credential
# data (Cookies, Login Data, History, Bookmarks, Preferences) — only the cache whitelist.
# The _lsof_in_use guard skips any dir a running Chrome holds open, so this is safe even
# during a live fleet browser task.
_CHROME_ROOT = pathlib.Path(os.path.expanduser(
    "~/Library/Application Support/Google/Chrome"))
_CHROME_PROFILE_CACHE_SUBPATHS = [
    "Cache", "Code Cache", "GPUCache", "GrShaderCache", "ShaderCache",
    "DawnGraphiteCache", "DawnWebGPUCache",
    "Service Worker/CacheStorage", "Service Worker/ScriptCache",
]
_CHROME_TOPLEVEL_CACHES = [
    "OptGuideOnDeviceModel", "ShaderCache", "GrShaderCache", "GraphiteDawnCache",
    "component_crx_cache",
]


def chrome_cache_candidates():
    """Regenerable Chrome cache dirs (per-profile + top-level); never profile/credential
    data. Empty list if Chrome isn't installed. fs read only."""
    if not _CHROME_ROOT.is_dir():
        return []
    out = []
    for prof in _CHROME_ROOT.iterdir():
        if not prof.is_dir():
            continue
        if prof.name in ("Default", "Guest Profile", "System Profile") \
                or prof.name.startswith("Profile"):
            for sub in _CHROME_PROFILE_CACHE_SUBPATHS:
                d = prof / sub
                if d.is_dir():
                    out.append(d)
    for name in _CHROME_TOPLEVEL_CACHES:
        d = _CHROME_ROOT / name
        if d.is_dir():
            out.append(d)
    return out


def _reclaim_chrome_cache(dry_run):
    """Clear regenerable Chrome caches. Mirrors _reclaim_next_cache's lsof-guarded rmtree
    so a running Chrome's open cache dirs are skipped (never corrupt a live session)."""
    freed = 0
    for d in chrome_cache_candidates():
        sz = _dir_size(d)
        if dry_run:
            freed += sz
            continue
        if _lsof_in_use(d):  # KILL-TIME re-verify — skip if Chrome is using it
            log(f"    chrome-cache {d} in use (lsof) — skipped")
            continue
        try:
            shutil.rmtree(d)
            freed += sz
        except OSError as e:
            raise RuntimeError(f"rmtree {d} failed: {e}") from e
    return freed


# Chrome code-sign clones (orch-console #77809, 2026-10-27): Chrome copies itself into
# <DARWIN_USER_DIR>/X/com.google.Chrome.code_sign_clone/code_sign_clone.XXXXXX on launch and
# never removes the old copies -- 19 had piled up (du 36GB, ~4GB real since the APFS clones
# share blocks) when found on a 98%-full Mini. The newest clone is ALWAYS kept (a Chrome
# mid-launch may not hold its files yet), and any clone a running Chrome holds is skipped by
# the kill-time lsof guard. None = resolve lazily from getconf (no subprocess at import).
CHROME_CLONE_ROOT = (pathlib.Path(os.environ["DISK_CHROME_CLONE_ROOT"])
                     if os.environ.get("DISK_CHROME_CLONE_ROOT") else None)


def _resolve_chrome_clone_root():
    if CHROME_CLONE_ROOT is not None:
        return CHROME_CLONE_ROOT
    try:
        r = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True,
                           text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    tmp = r.stdout.strip() if r.returncode == 0 else ""
    if not tmp:
        return None  # not macOS (or getconf refused) -- nothing to reap
    return pathlib.Path(tmp).parent / "X" / "com.google.Chrome.code_sign_clone"


def chrome_clone_candidates(root):
    """Every `code_sign_clone.*` dir under `root` EXCEPT the newest (by mtime). Pure --
    listdir/stat only. Symlinks and non-matching names are never candidates. Missing root ->
    empty."""
    if root is None:
        return []
    root = pathlib.Path(root)
    try:
        clones = [p for p in root.iterdir()
                  if p.name.startswith("code_sign_clone.") and p.is_dir() and not p.is_symlink()]
    except OSError:
        return []
    clones.sort(key=lambda p: p.stat().st_mtime)
    return clones[:-1]


def _reclaim_chrome_clones(dry_run):
    """Remove stale Chrome code-sign clones. Apply measures the DISK-FREE delta, not the dir
    sizes: the clones share APFS blocks, so summing _dir_size would claim ~9x what's freed.
    Dry-run still reports the (overstated) dir-size estimate -- there's no effect to measure."""
    cands = chrome_clone_candidates(_resolve_chrome_clone_root())
    if dry_run:
        return sum(_dir_size(d) for d in cands)
    before = _disk_free()
    for d in cands:
        if _lsof_in_use(d):  # KILL-TIME re-verify — a running Chrome holds this clone
            log(f"    chrome-clone {d} in use (lsof) — skipped")
            continue
        try:
            shutil.rmtree(d)
        except OSError as e:
            raise RuntimeError(f"rmtree {d} failed: {e}") from e
    after = _disk_free()
    if before is None or after is None:
        return 0
    return max(0, after - before)


def daily_backup_candidates(root, keep_n):
    """Dirs under `root` sorted by name (date-named -> lexicographic == chronological);
    return all but the newest `keep_n`. Pure -- listdir only, no deletion. Missing root
    or fewer than `keep_n` dirs -> empty (nothing to prune)."""
    root = pathlib.Path(root)
    try:
        entries = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return []
    if keep_n <= 0 or len(entries) <= keep_n:
        return [] if keep_n > 0 else entries
    return entries[:-keep_n]


def _reclaim_daily_backups(dry_run):
    """Prune the oldest daily backup dirs beyond BACKUP_KEEP_DAYS. Mirrors next-cache's
    lsof-guarded rmtree: an active backup write holds its dir open, so a lsof hit skips it
    rather than corrupting an in-progress snapshot."""
    freed = 0
    for d in daily_backup_candidates(BACKUPS_ROOT, BACKUP_KEEP_DAYS):
        sz = _dir_size(d)
        if dry_run:
            freed += sz
            continue
        if _lsof_in_use(d):
            log(f"    daily-backup {d} in use (lsof) — skipped")
            continue
        try:
            shutil.rmtree(d)
            freed += sz
        except OSError as e:
            raise RuntimeError(f"rmtree {d} failed: {e}") from e
    return freed


def library_caches_candidates(root):
    """Top-level entries under `root` (~/Library/Caches). Pure -- listdir only. Missing
    root -> empty."""
    root = pathlib.Path(root)
    try:
        return list(root.iterdir())
    except OSError:
        return []


#  Standing exemption (Nazim #64900, 2026-10-12): e2e lanes run webkit/chromium nightly,
#  so clearing this just re-downloads within hours (pure churn) AND disrupts an in-flight
#  e2e/render run if cleared mid-use. Never a clear candidate regardless of disk pressure --
#  the "page above 90% sustained" escalation for genuine emergencies lives in the severity
#  tier elsewhere in this file, not in this function deciding to delete it. Confirmed live
#  2026-10-26 (bus #76283, cc-console-revamp-1): this exemption was documented but never
#  actually implemented here, so disk_autoremediate wiped it ~every 5min and killed
#  console-deploy render gates 5x in one day before being root-caused.
_LIBRARY_CACHES_EXEMPT = {"ms-playwright"}


def _reclaim_library_caches(dry_run):
    """Clear ~/Library/Caches entries (Apple-blessed regenerable, per orch-console's own
    dig, bus #73708). Several subdirs are TCC/SIP-protected even for the owning user
    (CloudKit, HomeKit, Safari, etc) -- a PermissionError on one entry is EXPECTED. Others
    are actively written to by their owning process between our listing and our rmtree
    (bus #73901 live incident: com.apple.python raised OSError(ENOTEMPTY) from a TOCTOU
    race, not a permission error) -- Library/Caches entries are known-flaky in BOTH ways,
    so UNLIKE the other reclaimers' raise-on-OSError, ANY OSError here is logged + skipped
    WITHOUT raising: one stubborn/racing cache dir must never abort the whole reclaim pass
    or escalate into a false disk-emergency page (see page_severity's error/floor split)."""
    freed = 0
    for d in library_caches_candidates(LIBRARY_CACHES_ROOT):
        if d.name in _LIBRARY_CACHES_EXEMPT:
            log(f"    library-caches {d} exempt (standing rule, Nazim #64900) — skipped")
            continue
        try:
            sz = _dir_size(d) if d.is_dir() else d.stat().st_size
        except OSError:
            continue
        if dry_run:
            freed += sz
            continue
        if d.is_dir() and _lsof_in_use(d):
            log(f"    library-caches {d} in use (lsof) — skipped")
            continue
        try:
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
            freed += sz
        except OSError as e:
            log(f"    library-caches {d} skipped ({type(e).__name__}, protected/racing): {e}")
            continue
    return freed


def build_reclaimers():
    """Ordered safest/highest-known-yield first. npm/brew reclaimers are added only
    when the tool resolves to an absolute path — a missing tool is SKIPPED LOUDLY
    (logged) rather than added and left to FileNotFoundError at the critical tier."""
    reclaimers = [{"name": "file-history", "apply": _reclaim_file_history}]

    npm = _resolve_bin("npm")
    if npm:
        reclaimers.append({"name": "npm-cache", "apply": _reclaim_dir_cmd(
            os.path.expanduser("~/.npm"), [npm, "cache", "clean", "--force"])})
    else:
        log("    npm not found on PATH or in known bin dirs — npm-cache reclaimer unavailable")

    brew = _resolve_bin("brew")
    if brew:
        brew_cache = None
        try:
            r = subprocess.run([brew, "--cache"], capture_output=True, text=True, timeout=15,
                               env=_augmented_env())
            brew_cache = r.stdout.strip() or None
        except (subprocess.TimeoutExpired, OSError):
            brew_cache = None
        reclaimers.append({"name": "brew-cleanup", "apply": _reclaim_dir_cmd(
            brew_cache, [brew, "cleanup", "-s"], measure_via_disk_free=True)})
    else:
        log("    brew not found on PATH or in known bin dirs — brew-cleanup reclaimer unavailable")

    reclaimers.append({"name": "next-cache", "apply": _reclaim_next_cache})
    reclaimers.append({"name": "chrome-cache", "apply": _reclaim_chrome_cache})
    reclaimers.append({"name": "chrome-clones", "apply": _reclaim_chrome_clones})
    reclaimers.append({"name": "library-caches", "apply": _reclaim_library_caches})
    reclaimers.append({"name": "daily-backups", "apply": _reclaim_daily_backups})
    return reclaimers


# ---- fail-loud page ------------------------------------------------------------
def _page(free_before, free_after, summary, *, loud=True):
    """loud=True: P1 blocker bus row, console wakes (genuine emergency, i.e. a floor breach,
    optionally combined with a reclaimer error). loud=False: LOG-FILE ONLY (bus #73447 — a
    follow-up to #73430/PR#466; further decoupled from reclaimer errors per bus #73901) — no
    bus row at all. orch-console clarified that the console's reconcile-doorbell rings on ANY
    new unread row regardless of priority, so even a P3 bus row in the managed band was still
    indefinite console churn, just lower-stakes churn than P1. Two distinct loud=False cases
    now share this path: (a) the managed band (a real floor breach, already diagnosed
    structural with no new action), and (b) a HEALTHY disk where a reclaimer merely errored
    (a tooling bug, not a disk emergency — see page_severity). Both stay ENTIRELY local: the
    stderr banner (which lands in the launchd log) is the full record, never silent, just
    never a bus row. <4GB emergency, or any error COMBINED with an actual floor breach, still
    always gets the bus row via loud=True."""
    if not loud:
        healthy = free_after is not None and free_after >= FLOOR
        if healthy:
            banner = ("⚪ disk auto-remediation: disk HEALTHY, reclaimer error logged not paged "
                       "(log-only, no bus row)\n  "
                       f"free before={free_before // GB if free_before else '?'}GB "
                       f"after={free_after // GB if free_after else '?'}GB (floor {FLOOR // GB}GB) "
                       "— tooling bug, not a disk emergency (bus #73901).")
        else:
            banner = ("⚪ disk auto-remediation: managed band, no new action (log-only, no bus row)\n  "
                       f"free before={free_before // GB if free_before else '?'}GB "
                       f"after={free_after // GB if free_after else '?'}GB (floor {FLOOR // GB}GB, "
                       f"emergency {EMERGENCY_FLOOR // GB}GB) — already diagnosed structural "
                       f"(bus #73430/#73447), no new action.")
        for r in summary["results"]:
            banner += f"\n    reclaimed {r['name']}: ~{r['freed'] // GB}GB"
        for e in summary["errors"]:
            banner += f"\n    🔴 {e['name']} FAILED: {e['error']}"
        print(banner, file=sys.stderr, flush=True)
        return
    header = "🔴 DISK AUTO-REMEDIATION INSUFFICIENT"
    tldr = (f"TL;DR: disk auto-remediation ran but free is STILL below the "
            f"{FLOOR // GB}GB floor (or a reclaimer errored) — needs a look before the "
            f"fleet crashes on [Errno 28].")
    mtype, priority = "blocker", "P1"
    subject = "disk auto-remediation insufficient — free still below floor"
    lines = [
        tldr,
        f"free before={free_before // GB if free_before else '?'}GB "
        f"after={free_after // GB if free_after else '?'}GB (floor {FLOOR // GB}GB, "
        f"emergency {EMERGENCY_FLOOR // GB}GB).",
    ]
    for r in summary["results"]:
        lines.append(f"  reclaimed {r['name']}: ~{r['freed'] // GB}GB")
    for e in summary["errors"]:
        lines.append(f"  🔴 {e['name']} FAILED: {e['error']}")
    banner = header + "\n  " + "\n  ".join(lines)
    print(banner, file=sys.stderr, flush=True)
    try:
        try:
            from scripts import bus_send
        except ImportError:
            import bus_send  # type: ignore
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if dsn:
            bus_send.send(from_agent="cc-fleet-health", to="orch-console", mtype=mtype,
                          subject=subject, body="\n".join(lines), priority=priority,
                          req=False, dsn=dsn)
    except Exception as e:  # noqa: BLE001 — page best-effort; stderr banner already stands
        print(f"[disk-autoremediate] WARN bus-page failed ({e}); banner stands", file=sys.stderr)


# ---- Spotlight index guard (orch-console #77912, 2026-10-27) ---------------------------
# The Mini's "unattributed ~90Gi" was /System/Volumes/Data/.Spotlight-V100: a 69GB index
# from 08-28, left on disk after indexing was disabled. du from this user can't see inside
# it (root-owned, TCC), and no reclaimer could touch it. With indexing OFF the directory
# must not exist at all, so its reappearance -- or indexing turning back on -- is the
# signal. Remediation needs root (`mdutil -X`), which this job doesn't hold, so it PAGES
# with the exact fix instead of acting. At most once per SPOTLIGHT_PAGE_EVERY_S per
# distinct finding-set; the memory clears once healthy.
SPOTLIGHT_INDEX_DIR = pathlib.Path(os.environ.get(
    "DISK_SPOTLIGHT_INDEX_DIR", "/System/Volumes/Data/.Spotlight-V100"))
SPOTLIGHT_VOLUMES = ("/", "/System/Volumes/Data")
SPOTLIGHT_PAGE_EVERY_S = int(os.environ.get("DISK_SPOTLIGHT_PAGE_EVERY_S", str(24 * 3600)))
SPOTLIGHT_STATE = pathlib.Path(os.environ.get(
    "DISK_SPOTLIGHT_STATE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                 "spotlight_guard_state.json")))
_SPOTLIGHT_OFF_MARKERS = ("server is disabled", "indexing disabled", "no index")


def _mdutil_status(volume):
    try:
        r = subprocess.run(["mdutil", "-s", volume], capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr).strip()
    except (subprocess.TimeoutExpired, OSError):
        return None


def spotlight_findings(index_dir=None, status=_mdutil_status, volumes=SPOTLIGHT_VOLUMES):
    """Problems to page about (empty = healthy). An unreadable mdutil status is NOT a
    finding -- this guard only pages on positive evidence."""
    index_dir = pathlib.Path(index_dir or SPOTLIGHT_INDEX_DIR)
    out = []
    if os.path.lexists(index_dir):
        out.append(f"{index_dir} exists again (indexing is meant to be OFF; it grew to 69GB "
                   f"unnoticed before)")
    for vol in volumes:
        st = status(vol)
        if st and not any(m in st.lower() for m in _SPOTLIGHT_OFF_MARKERS):
            out.append(f"Spotlight indexing looks ENABLED on {vol}: {st.splitlines()[-1][:120]}")
    return out


def spotlight_guard(*, findings=None, now=None, state_path=None, page=None):
    """Run the check; page orch-console when due. Returns the findings."""
    findings = spotlight_findings() if findings is None else findings
    now = time.time() if now is None else now
    state_path = pathlib.Path(state_path or SPOTLIGHT_STATE)
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    if not findings:
        if state:
            try:
                state_path.unlink()
            except OSError:
                pass
        return findings
    key = "|".join(sorted(findings))
    if state.get("key") == key and now - state.get("paged_at", 0) < SPOTLIGHT_PAGE_EVERY_S:
        return findings
    (page or _page_spotlight)(findings)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"key": key, "paged_at": now}))
    except OSError as e:
        log(f"[disk-autoremediate] WARN spotlight guard state not saved ({e})")
    return findings


def _page_spotlight(findings):
    lines = ["TL;DR: the Spotlight index is coming back on the Mini. It silently ate 69GB "
             "last time (08-28 to 10-27). Needs a root fix."]
    lines += [f"  - {f}" for f in findings]
    lines += ["Fix (root + Full Disk Access; plain sudo from tmux is TCC-blocked): "
              "ssh localhost, then `sudo mdutil -i off /` and `sudo mdutil -i off "
              "/System/Volumes/Data`, then `sudo mdutil -X /System/Volumes/Data`. Verify with "
              "`mdutil -s` and a df delta."]
    print("\n".join(lines), file=sys.stderr, flush=True)
    try:
        import psycopg2
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if dsn:
            c = psycopg2.connect(dsn)
            cur = c.cursor()
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute("""INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,subject,body)
                           VALUES ('cc-fleet-health','orch-console','blocker','P2',%s,%s)""",
                        ("Spotlight index regrowing on the Mini — needs root mdutil -X",
                         "\n".join(lines)))
            c.commit()
    except Exception as e:  # noqa: BLE001 — page best-effort; stderr banner already stands
        print(f"[disk-autoremediate] WARN spotlight page failed ({e}); banner stands",
              file=sys.stderr)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    mode = "APPLIED" if apply else "DRY-RUN"
    free = _disk_free()
    if free is None:
        print(f"[disk-autoremediate] ERROR could not read {DATA_VOL} free", file=sys.stderr)
        return 1
    if apply:
        try:
            spotlight_guard()
        except Exception as e:  # noqa: BLE001 — a guard bug must never block disk reclaim
            log(f"[disk-autoremediate] WARN spotlight guard failed: {e!r}")
    tier = decide(free, remediate_under=REMEDIATE_UNDER, floor=FLOOR)
    log(f"[disk-autoremediate {mode}] free={free // GB}GB tier={tier} "
        f"(remediate<{REMEDIATE_UNDER // GB}GB floor={FLOOR // GB}GB "
        f"emergency={EMERGENCY_FLOOR // GB}GB)")
    if tier == "ok":
        return 0
    summary = run_reclaimers(build_reclaimers(), free_probe=_disk_free,
                             target=REMEDIATE_UNDER, dry_run=not apply)
    free_after = _disk_free()
    total_freed = sum(r["freed"] for r in summary["results"])
    log(f"[disk-autoremediate {mode}] {'would free' if not apply else 'freed'} "
        f"~{total_freed // GB}GB total; free after={free_after // GB if free_after else '?'}GB; "
        f"errors={len(summary['errors'])}")
    # Fail-loud PAGE (apply mode only) — severity-gated per bus #73430 (see page_severity doc).
    if apply:
        severity = page_severity(free_after, summary["errors"],
                                 floor=FLOOR, emergency_floor=EMERGENCY_FLOOR)
        if severity != "none":
            _page(free, free_after, summary, loud=(severity == "loud"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
