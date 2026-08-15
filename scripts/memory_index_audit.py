"""memory_index_audit.py — keep every agent's memory INDEX honest.

WHY (operator, 2026-08-15/16): each agent's memory is a folder of markdown files plus a
MEMORY.md index, and ONLY the index is loaded at boot. That creates two silent-loss modes,
both of which were live when this was written:

  1. ORPHANED — a memory file exists but no index line points at it, so nothing ever loads
     it. It is invisible from birth. Writing the file and writing the index line are two
     separate steps with nothing binding them, so they drift. 23 orphans across 3 agents
     when first swept, including a note written the same day about the operator wanting the
     fleet to PROPOSE rather than execute.
  2. OVERSIZE — the index is read whole, under a limit. Past that limit it truncates and
     whatever falls off the end stops existing, with no error. The orchestrator index was
     20.1KB against a 24.4KB read limit; cc-ihsanos is at 20.4KB now.

Neither failure announces itself, which is exactly why this is a checker and not a habit
(feedback_enforce_process_in_code_not_promises). A memory system that silently forgets is
worse than one that is obviously empty, because it is trusted.

--fix APPENDS missing index lines, generated from each file's own frontmatter `description`.
That is deliberately the only mutation offered: appending strictly INCREASES what is
visible and is trivially reversible. Pruning, rewording and re-grouping stay manual —
an auto-editor loose in another agent's memory is a worse problem than a missing line.

Usage:
  python scripts/memory_index_audit.py             # audit all agents, exit 1 on any problem
  python scripts/memory_index_audit.py --fix       # also append missing index lines
  python scripts/memory_index_audit.py --alert     # post a P2 bus row per affected agent
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
INDEX = "MEMORY.md"

# The index is read whole at boot under a hard limit. Warn well below it: an index that is
# merely UNDER the limit today is one memory away from truncating tomorrow, and the whole
# point is to not find out by losing something.
READ_LIMIT_BYTES = 24_400
WARN_BYTES = 17_100

LINK_RE = re.compile(r"\(([A-Za-z0-9_.-]+\.md)\)")


def _description(path: pathlib.Path) -> str:
    """The file's own one-line summary, used to generate an index entry. Frontmatter only —
    never the body, which is the memory itself and would bloat the index it is fixing."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"^description:\s*(.+?)\s*$", text, re.M)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def audit(mem_dir: pathlib.Path) -> dict:
    index_path = mem_dir / INDEX
    files = sorted(p for p in mem_dir.glob("*.md") if p.name != INDEX)
    if not index_path.exists():
        return {"dir": mem_dir, "missing_index": True, "files": len(files),
                "orphans": [p.name for p in files], "dangling": [], "size": 0}

    index_text = index_path.read_text(encoding="utf-8", errors="replace")
    linked = set(LINK_RE.findall(index_text))
    present = {p.name for p in files}

    return {
        "dir": mem_dir,
        "missing_index": False,
        "files": len(files),
        # Exists on disk, nothing points at it -> never loaded, invisible.
        "orphans": sorted(present - linked),
        # Index points at a file that is gone -> a promise the index cannot keep.
        "dangling": sorted(linked - present),
        "size": len(index_text.encode("utf-8")),
    }


def fix(result: dict) -> int:
    """Append an index line for each orphan. Returns how many were added."""
    mem_dir = result["dir"]
    index_path = mem_dir / INDEX
    if not result["orphans"]:
        return 0
    lines = []
    for name in result["orphans"]:
        desc = _description(mem_dir / name)
        title = name[:-3].replace("_", " ")
        hook = f" - {desc}" if desc else ""
        lines.append(f"- [{title}]({name}){hook}\n")
    with index_path.open("a", encoding="utf-8") as fh:
        if not index_path.read_text(encoding="utf-8", errors="replace").endswith("\n"):
            fh.write("\n")
        fh.writelines(lines)
    return len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true",
                    help="append index lines for orphaned memory files")
    ap.add_argument("--alert", action="store_true",
                    help="post a P2 bus row for each agent still failing after the run")
    a = ap.parse_args()

    if not PROJECTS.is_dir():
        print(f"no projects dir at {PROJECTS}", file=sys.stderr)
        return 1

    problems = []
    for mem_dir in sorted(PROJECTS.glob("*/memory")):
        if not mem_dir.is_dir():
            continue
        r = audit(mem_dir)
        added = fix(r) if (a.fix and r["orphans"] and not r["missing_index"]) else 0
        if added:
            r = audit(mem_dir)  # re-audit so the report reflects reality, not intent
            r["added"] = added

        label = mem_dir.parent.name
        flags = []
        if r["missing_index"]:
            flags.append("NO-INDEX")
        if r["orphans"]:
            flags.append(f"ORPHANED={len(r['orphans'])}")
        if r["dangling"]:
            flags.append(f"DANGLING={len(r['dangling'])}")
        if r["size"] >= READ_LIMIT_BYTES:
            flags.append(f"OVER-READ-LIMIT({r['size']}B)")
        elif r["size"] >= WARN_BYTES:
            flags.append(f"NEAR-LIMIT({r['size']}B)")

        status = " ".join(flags) if flags else "ok"
        extra = f" (+{r.get('added')} added)" if r.get("added") else ""
        print(f"{label:<58} files={r['files']:<4} idx={r['size']:<6}B {status}{extra}")
        if r["orphans"]:
            for n in r["orphans"][:10]:
                print(f"    orphan: {n}")
        for n in r["dangling"]:
            print(f"    dangling: {n}")
        if flags:
            problems.append((label, status, r))

    if not problems:
        print("\nALL CLEAN — every memory file is indexed, every link resolves, "
              "every index is under the read limit.")
        return 0

    print(f"\n{len(problems)} agent memory dir(s) need attention.")
    if a.alert:
        _alert(problems)
    return 1


def _alert(problems: list) -> None:
    """LOUD, per the dead-man's-switch rule: a checker that finds a silent-loss condition
    and then reports it only to a log nobody reads has reproduced the bug it detects."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import psycopg
        from dotenv import load_dotenv
        root = pathlib.Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        body_lines = [
            "Memory-index audit found silent-loss conditions. An ORPHANED file is never "
            "loaded at boot (invisible from birth); an OVER-LIMIT index truncates on read "
            "and whatever falls off the end stops existing, with no error.",
            "",
        ]
        for label, status, r in problems:
            body_lines.append(f"  {label}: {status}")
            for n in r["orphans"][:10]:
                body_lines.append(f"      orphan: {n}")
        body_lines += [
            "",
            "FIX: `python scripts/memory_index_audit.py --fix` appends the missing index "
            "lines from each file's own frontmatter description (append-only, reversible). "
            "An over-limit index needs a manual compaction pass — group related entries onto "
            "one line and trim hooks; never drop a link.",
        ]
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                "priority,requires_response) VALUES "
                "('orch-console','cc-fleet-health','question',%s,%s,'P2',true)",
                (f"memory-index audit: {len(problems)} agent memory dir(s) losing entries silently",
                 "\n".join(body_lines)))
        print("posted alert bus row to cc-fleet-health")
    except Exception as exc:  # noqa: BLE001 — the alarm must never take down the checker
        print(f"WARN: could not post alert bus row: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
