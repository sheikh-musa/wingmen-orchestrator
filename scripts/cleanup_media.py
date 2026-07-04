#!/usr/bin/env python3
"""cleanup_media.py — retention + abrogation cleanup for the console media store
(logs/tg_media). Keeps the store from bloating as cc-orch routes every lane
preview/screenshot into it.

SAFE BY DEFAULT:
  - default mode = DRY-RUN (reports what it WOULD archive; touches nothing)
  - --apply ARCHIVES (moves, reversible) candidates to logs/tg_media/_archive/<project>/
  - --purge-archive-older-than DAYS is the ONLY destructive op (hard-deletes from
    _archive). Separate flag on purpose — never runs unless explicitly asked.

WHAT IT RETIRES (per project folder):
  1. OLD/EXCESS: beyond KEEP_LATEST_N newest AND older than KEEP_RECENT_DAYS.
  2. ABROGATED (superseded): group by a normalized stem (strip _LIVE / _v\\d+ /
     trailing dates); within a stem-group keep the newest, retire older siblings
     that are past SUPERSEDED_GRACE_DAYS. (A re-shot "LIVE" image supersedes the
     pre-deploy one, etc.)

NEVER auto-touches EXCLUDE_PROJECTS (operator-inbound / personal) or _archive.

Usage:
  cleanup_media.py                         # dry-run report
  cleanup_media.py --apply                 # archive candidates (reversible)
  cleanup_media.py --purge-archive-older-than 60   # hard-delete old archive (destructive)
"""
import argparse, os, re, pathlib, shutil, time

ORCH = pathlib.Path(__file__).resolve().parent.parent
ROOT = ORCH / "logs" / "tg_media"
ARCHIVE = ROOT / "_archive"
EXCLUDE_PROJECTS = {"_archive", "_personal"}  # operator-inbound INCLUDED per operator 2026-06-28; _personal stays protected
KEEP_LATEST_N = 12          # always keep the N newest per project
KEEP_RECENT_DAYS = 30       # always keep anything newer than this
SUPERSEDED_GRACE_DAYS = 3   # a superseded sibling must be at least this old to retire
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_STRIP = re.compile(r"(_live|_v\d+|_\d{6,8}|_\d{10,}|_final|_new|_old|_copy)", re.I)

def _stem_family(name: str) -> str:
    base = re.sub(r"\.[a-z0-9]+$", "", name, flags=re.I)
    base = _STRIP.sub("", base)
    return re.sub(r"_+", "_", base).strip("_").lower()

def _now() -> float:
    return time.time()

def scan(project_dir: pathlib.Path):
    files = []
    for dp, _dn, fn in os.walk(project_dir):
        for f in fn:
            p = pathlib.Path(dp) / f
            if p.suffix.lower() in IMG_EXT:
                files.append(p)
    return files

def candidates(project_dir: pathlib.Path):
    files = scan(project_dir)
    if not files:
        return [], len(files)
    now = _now()
    by_mtime = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    protected = set(by_mtime[:KEEP_LATEST_N])
    for p in files:
        if (now - p.stat().st_mtime) < KEEP_RECENT_DAYS * 86400:
            protected.add(p)
    retire = set()
    # 1. old/excess
    for p in files:
        if p not in protected:
            retire.add(p)
    # 2. abrogated/superseded
    fam = {}
    for p in files:
        fam.setdefault(_stem_family(p.name), []).append(p)
    for _stem, members in fam.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for older in members[1:]:
            if (now - older.stat().st_mtime) >= SUPERSEDED_GRACE_DAYS * 86400:
                retire.add(older)
    return sorted(retire), len(files)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="archive candidates (reversible move)")
    ap.add_argument("--purge-archive-older-than", type=int, metavar="DAYS",
                    help="DESTRUCTIVE: hard-delete archived files older than DAYS")
    args = ap.parse_args()
    if not ROOT.exists():
        print(f"no media root: {ROOT}"); return

    total_retire = total_bytes = 0
    for proj in sorted([d for d in ROOT.iterdir() if d.is_dir()], key=lambda p: p.name.lower()):
        if proj.name in EXCLUDE_PROJECTS:
            continue
        cands, n = candidates(proj)
        if not cands:
            continue
        b = sum(p.stat().st_size for p in cands)
        total_retire += len(cands); total_bytes += b
        print(f"[{proj.name}] {n} files · retire {len(cands)} ({b//1024} KB)")
        for p in cands:
            rel = p.relative_to(ROOT)
            if args.apply:
                dest = ARCHIVE / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                print(f"   archived: {rel}")
            else:
                print(f"   would archive: {rel}")
    print(f"\n{'ARCHIVED' if args.apply else 'WOULD ARCHIVE'}: {total_retire} files, {total_bytes//1024} KB"
          f"  (mode: {'APPLY' if args.apply else 'DRY-RUN'})")

    if args.purge_archive_older_than is not None and ARCHIVE.exists():
        cutoff = _now() - args.purge_archive_older_than * 86400
        purged = pb = 0
        for dp, _dn, fn in os.walk(ARCHIVE):
            for f in fn:
                p = pathlib.Path(dp) / f
                if p.stat().st_mtime < cutoff:
                    pb += p.stat().st_size; purged += 1; p.unlink()
        print(f"PURGED from archive (>{args.purge_archive_older_than}d): {purged} files, {pb//1024} KB")

if __name__ == "__main__":
    main()
