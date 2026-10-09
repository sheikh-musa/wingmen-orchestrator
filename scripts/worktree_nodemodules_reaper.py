"""worktree_nodemodules_reaper — routine Tier-1 node_modules/.next hygiene for named PR
worktree families (cc-fleet-health §4.6).

WHY (orch-console #61045→#61047, 2026-10-09): the Mini DATA volume flaps at the console warn
tier (~88%, ~26Gi free) but that is ABOVE disk_autoremediate's 20Gi pre-crash floor, so the
pre-crash reclaimer correctly stays idle while the warn keeps re-firing. The real recurring
consumer is ~800MB of regenerable `node_modules` (plus `.next`) in each `cosem-platform.wt-*`
(and `ihsanos-irsyad.wt-*`) PR worktree — many of them ABANDONED (no live process). A manual
clear freed ~5.8GB (88%→86%) but it recurs because nothing periodically clears the abandoned
trees. This closes that detect->act loop as ROUTINE hygiene, not pressure-gated remediation.

SCOPE IS DELIBERATELY NARROW (Tier-1 only, orch-console #61047): this clears ONLY `node_modules`
and `.next` — gitignored, regenerable (`npm/pnpm install`), git state UNTOUCHED, fully reversible.
It does NOT remove worktrees (those `*.wt-*` branches are local-only + unmerged; removal is the
operator/cai-gated Tier-2 handled elsewhere) and it NEVER deletes a tracked/non-ignored dir.

DISCIPLINE (CLAUDE.md §2):
  - Verify before act: a candidate dir is cleared only if git considers it IGNORED (so a
    pathological committed node_modules is never clobbered). Fail-safe: if the ignore check
    errors, the dir is SPARED.
  - Reversible: node_modules/.next are regenerable; git tracked state is never touched.
  - Kill-time protection (op#15328): a worktree with ANY live process cwd'd at/under it is
    SPARED ENTIRELY, re-checked immediately before each removal (a tree can go live between the
    scan and the rm). lsof errors FAIL-SAFE = treat as in-use (spare).
  - Dead-man / fail-loud: a per-dir removal failure is logged LOUDLY and recorded, never
    swallowed; removals are INDEPENDENT (one failure never aborts the sweep); --apply PAGES the
    bus (orch-console) if any removal errored.
  - Staged arming: DETECT-ONLY by default (reports what it WOULD free, touches nothing). --apply
    acts. Wet-proven live 2026-10-09 (the manual 5.8GB clear) before arming.

Usage (from ~/wingmen/orchestrator):
  .venv/bin/python3 -m scripts.worktree_nodemodules_reaper            # DETECT-ONLY
  .venv/bin/python3 -m scripts.worktree_nodemodules_reaper --apply    # clear for real (ARMED)
  .venv/bin/python3 -m scripts.worktree_nodemodules_reaper --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

GB = 1 << 30
MB = 1 << 20

# --- tunables (env-overridable) ---
# Colon-separated glob patterns (each expands to candidate worktree dirs). Default = the family
# orch-console #61047 approved; ihsanos-irsyad.wt-* is the same mechanism and can be added here.
FAMILIES = [p for p in os.environ.get(
    "NM_REAPER_FAMILIES",
    "~/wingmen/projects/cosem-platform.wt-*",
).split(":") if p]
# dir names cleared (gitignored + regenerable); env-overridable for tests/extension.
RECLAIM_NAMES = tuple(n for n in os.environ.get(
    "NM_REAPER_RECLAIM_NAMES", "node_modules:.next").split(":") if n)
LSOF_TIMEOUT_S = int(os.environ.get("NM_REAPER_LSOF_TIMEOUT_S", "30"))
GIT_TIMEOUT_S = int(os.environ.get("NM_REAPER_GIT_TIMEOUT_S", "30"))
# A worktree with ANY build-indicator dir modified more recently than this is treated as having
# an ACTIVE BUILD and is SPARED ENTIRELY. WHY (#61165 regression, orch-console #61169 steer): the
# lsof-cwd gate is not enough — a live lane can build CROSS-WORKTREE (cwd in wt-calendar, `npm run
# build` in wt-tablet-onboarding), so "no cwd in the tree" does NOT mean abandoned. The reaper
# wiped a just-reinstalled node_modules out from under a live APK build. The fix (per #61169) is a
# PER-TREE recent-activity check on build outputs — reclaims genuinely-idle trees even while the
# project's other lanes are busy, but protects any tree with a live/recent build.
MIN_AGE_H = float(os.environ.get("NM_REAPER_MIN_AGE_H", "12"))
MIN_AGE_S = MIN_AGE_H * 3600
# Dir names whose recent mtime signals an active build (checked at the tree root AND under apps/*).
INDICATOR_NAMES = tuple(n for n in os.environ.get(
    "NM_REAPER_INDICATOR_NAMES",
    "node_modules:.next:dist:build:out:android:.gradle:.turbo",
).split(":") if n)


def log(msg: str) -> None:
    print(msg, flush=True)


# ---- pure decision (fully unit-testable; no I/O) -------------------------------
def classify(*, has_git: bool, in_use: bool, recently_active: bool) -> str:
    """Verdict for ONE worktree. Cheapest/safest gate first.
    - no-git: the family glob matched a non-worktree dir — do NOT touch (report it).
    - in-use: a live process holds a cwd at/under it (kill-time protection). SPARE.
    - active: a build-indicator dir was modified recently — a live/recent build (possibly from a
      lane cwd'd in a SIBLING worktree, #61165) is using this tree. SPARE the whole tree.
    - clear:  idle worktree, no recent build → its gitignored node_modules/.next are reclaimable."""
    if not has_git:
        return "skip:no-git"
    if in_use:
        return "skip:in-use"
    if recently_active:
        return "skip:active"
    return "clear"


# ---- fs / git probes (best-effort, fail-safe) ---------------------------------
def find_family_worktrees(patterns, *, home=None):
    """Expand each glob pattern to existing dirs, de-duplicated by real path, sorted."""
    home = pathlib.Path(home) if home else pathlib.Path.home()
    seen, out = set(), []
    for pat in patterns:
        pat = os.path.expanduser(pat) if not home else pat.replace("~", str(home), 1)
        base = pathlib.Path(pat)
        try:
            matches = sorted(base.parent.glob(base.name))
        except OSError:
            continue
        for d in matches:
            if not d.is_dir():
                continue
            try:
                rp = d.resolve()
            except OSError:
                rp = d
            if rp not in seen:
                seen.add(rp)
                out.append(d)
    return out


def _has_git(path: pathlib.Path) -> bool:
    # a git worktree has a .git file (gitdir pointer) or dir
    return (path / ".git").exists()


def _lsof_cwd_in_use(path: pathlib.Path) -> bool:
    """True if any live process has its CWD at or under `path`. FAIL-SAFE: on any lsof error
    return True (treat as in-use) so we never delete under something we could not verify idle."""
    try:
        r = subprocess.run(["lsof", "-a", "-d", "cwd", "-F", "n", "--", str(path)],
                           capture_output=True, text=True, timeout=LSOF_TIMEOUT_S)
        # lsof exits non-zero (1) when there are simply no matching open files — that is NOT an
        # error, it means idle. Distinguish: real errors print to stderr.
        if r.returncode not in (0, 1):
            return True
        prefix = str(path)
        for line in r.stdout.splitlines():
            if line.startswith("n"):
                name = line[1:]
                if name == prefix or name.startswith(prefix + os.sep):
                    return True
        return False
    except (subprocess.TimeoutExpired, OSError):
        return True


def _is_ignored(wt: pathlib.Path, d: pathlib.Path) -> bool:
    """True iff git considers `d` IGNORED within worktree `wt` (safe to delete). FAIL-SAFE:
    any error → False (NOT ignored → spare it; never clobber a possibly-tracked dir)."""
    try:
        r = subprocess.run(["git", "-C", str(wt), "check-ignore", "-q", str(d)],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _indicator_dirs(wt: pathlib.Path, names=INDICATOR_NAMES):
    """Build-indicator dirs to check for recent activity: each name at the tree root and under
    every apps/* subdir (monorepos build in apps/<x>/). Cheap, bounded — no full-tree walk."""
    roots = [wt]
    apps = wt / "apps"
    if apps.is_dir():
        try:
            roots += [p for p in apps.iterdir() if p.is_dir()]
        except OSError:
            pass
    out = []
    for r in roots:
        for n in names:
            out.append(r / n)
    return out


def _tree_recently_active(wt: pathlib.Path, min_age_s: float, *, names=INDICATOR_NAMES) -> bool:
    """True if ANY build-indicator dir in the tree has mtime newer than min_age_s (an active or
    recent build — e.g. a just-reinstalled node_modules or a fresh build output). SPARE the whole
    tree when so (#61165). FAIL-SAFE: if a stat raises, treat THAT dir as fresh (return True)."""
    now = time.time()
    for d in _indicator_dirs(wt, names):
        try:
            if d.exists() and (now - d.stat().st_mtime) < min_age_s:
                return True
        except OSError:
            return True  # cannot verify → assume active → spare
    return False


def find_reclaimable_dirs(wt: pathlib.Path, names=RECLAIM_NAMES):
    """All `node_modules`/`.next` dirs within `wt` (pruned — does not descend into a match).
    Best-effort walk; unreadable subtrees are skipped."""
    out = []
    try:
        for dirpath, dirnames, _ in os.walk(wt):
            keep = []
            for dn in dirnames:
                if dn in names:
                    out.append(pathlib.Path(dirpath) / dn)
                else:
                    keep.append(dn)
            dirnames[:] = keep  # prune: don't descend into a matched (about-to-delete) dir
    except OSError:
        pass
    return out


def _dir_size(path: pathlib.Path) -> int:
    total = 0
    try:
        for dp, _, files in os.walk(path):
            for f in files:
                try:
                    total += (pathlib.Path(dp) / f).lstat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _rmtree(path: pathlib.Path) -> None:
    import shutil
    shutil.rmtree(path)


# ---- sweep --------------------------------------------------------------------
def reap_one(wt: pathlib.Path, *, dry_run: bool):
    """Clear reclaimable dirs in ONE worktree. Returns a report dict. Fail-loud per dir."""
    rep = {"worktree": str(wt), "verdict": None, "freed": 0, "cleared": [], "errors": []}
    has_git = _has_git(wt)
    in_use = _lsof_cwd_in_use(wt) if has_git else False
    # PER-TREE recent-activity gate (#61165/#61169): only check if cheaper gates haven't spared it
    recently_active = (_tree_recently_active(wt, MIN_AGE_S)
                       if (has_git and not in_use) else False)
    verdict = classify(has_git=has_git, in_use=in_use, recently_active=recently_active)
    rep["verdict"] = verdict
    if verdict != "clear":
        return rep
    for d in find_reclaimable_dirs(wt):
        if not d.is_dir():
            continue
        if not _is_ignored(wt, d):
            rep["errors"].append(f"NOT-IGNORED (spared, would clobber tracked): {d}")
            continue
        # KILL-TIME re-verify: the worktree may have gone live since the top-level scan.
        if _lsof_cwd_in_use(wt):
            rep["errors"].append(f"went LIVE at kill-time (spared): {wt}")
            rep["verdict"] = "skip:in-use(kill-time)"
            break
        sz = _dir_size(d)
        if dry_run:
            rep["cleared"].append({"dir": str(d), "bytes": sz, "dry_run": True})
            rep["freed"] += sz
            continue
        try:
            _rmtree(d)
            rep["cleared"].append({"dir": str(d), "bytes": sz})
            rep["freed"] += sz
        except OSError as e:
            rep["errors"].append(f"rm failed {d}: {e}")
    return rep


def _page(summary):  # pragma: no cover - exercised via bus in prod
    """Fail-loud bus page to orch-console when a removal errored (apply mode only)."""
    try:
        from scripts import bus_send  # type: ignore
    except Exception:
        try:
            import bus_send  # type: ignore
        except Exception:
            log("PAGE-FALLBACK (bus_send import failed): " + json.dumps(summary))
            return
    body = ["TL;DR: worktree node_modules reaper hit removal error(s) — see below.", ""]
    for e in summary["errors"]:
        body.append(f"  ERROR: {e}")
    try:
        bus_send.send(to="orch-console", from_agent="cc-fleet-health", type="blocker",
                      priority="P2", subject="worktree node_modules reaper: removal error(s)",
                      body="\n".join(body), requires_response=False)
    except Exception as e:
        log(f"PAGE-FALLBACK (bus_send.send failed: {e}): " + json.dumps(summary))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="clear for real (default: detect-only)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    dry_run = not args.apply
    mode = "APPLY" if args.apply else "DETECT-ONLY"

    worktrees = find_family_worktrees(FAMILIES)
    reports = [reap_one(wt, dry_run=dry_run) for wt in worktrees]
    total_freed = sum(r["freed"] for r in reports)
    all_errors = [e for r in reports for e in r["errors"]]
    cleared_n = sum(1 for r in reports if r["cleared"])

    summary = {"mode": mode, "worktrees": len(worktrees), "cleared_worktrees": cleared_n,
               "freed_bytes": total_freed, "errors": all_errors, "reports": reports}

    if args.json:
        log(json.dumps(summary, indent=2))
    else:
        log(f"[nm-reaper {mode}] scanned={len(worktrees)} "
            f"would_free~{total_freed // MB}MB ({total_freed / GB:.1f}GB) "
            f"errors={len(all_errors)}")
        for r in reports:
            if r["cleared"] or r["errors"]:
                tag = "WOULD-CLEAR" if dry_run else "CLEARED"
                log(f"  {tag} {pathlib.Path(r['worktree']).name}: "
                    f"~{r['freed'] // MB}MB [{r['verdict']}]")
            for e in r["errors"]:
                log(f"    ! {e}")

    if args.apply and all_errors:
        _page(summary)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
