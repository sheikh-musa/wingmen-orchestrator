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
import json
import os
import pathlib
import re
import sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
INDEX = "MEMORY.md"
# Optional overflow index (the core/tail split piloted on orch-console 2026-08-16). Only
# MEMORY.md is auto-loaded at boot; when an agent's memory outgrows that budget, the long tail
# of entries moves here and the core keeps a pointer plus the complete TOPIC list, so an agent
# always knows a subject exists even when the per-file links are one grep away.
TAIL_INDEX = "MEMORY-INDEX.md"
INDEX_FILES = (INDEX, TAIL_INDEX)

# The index is read whole at boot under a hard limit; past it the tail truncates silently.
#
# The warn is HEADROOM-based, not an absolute byte count — corrected 2026-08-16 after the
# first version used a flat 17.1KB, which was really just "whatever the orchestrator index
# happened to weigh". cc-ihsanos compacted to 17852B with 6.5KB of headroom, was still
# flagged, and went hunting for a phantom "something is re-adding bytes" to chase the last
# 750B. A threshold that makes a healthy agent do pointless work is a bad threshold: it
# should signal real risk, not set a target. 20% headroom is roughly a dozen more memories
# of room — enough warning to act, late enough to mean something.
READ_LIMIT_BYTES = 24_400
WARN_HEADROOM_FRACTION = 0.20
WARN_BYTES = int(READ_LIMIT_BYTES * (1 - WARN_HEADROOM_FRACTION))  # 19_520

# Alert dedup memory (Nazim 38293): a stable ADVISORY (NEAR-LIMIT) re-paging every daily run is
# noise. We remember which dirs we've already advised so we don't re-page — persisted across runs
# like the dead-man's `_paged_today`. If this file is lost/reset, we re-alert ONCE (fail toward
# alerting, never silent-suppress-forever). HARD flags are never recorded here — they page every run.
SRE_AGENT = "cc-fleet-health"
_STATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "state"
ALERT_STATE_PATH = _STATE_DIR / "memory_index_audit_alerts.json"

# Require the markdown-link `](target.md)` close-bracket, not a bare `(x.md)`: without the
# `\]` this matched parenthetical prose INSIDE a link label — e.g. the entry
# "[compact a memory index → KEEP (file.md) link syntax](feedback_...md)" reported a phantom
# DANGLING edge to a non-existent `file.md`. Verified fleet-wide (2026-08-21): the `\]` drops
# ONLY that one phantom, no real edge anywhere. (cc-fleet-health, per orch-console #31251.)
LINK_RE = re.compile(r"\]\(([A-Za-z0-9_.-]+\.md)\)")
# Memories cross-reference each other with [[wiki-links]]. Those edges are the substrate's
# existing knowledge graph — 616 of them across 209 of the orchestrator's 221 memories when
# first measured — and until this checker existed NOTHING verified they resolved. A broken
# edge fails exactly like an orphaned file: silently, and only when you needed it.
WIKI_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
# A [[link]] to a memory that does not exist yet is LEGITIMATE per the memory doctrine — it
# marks something worth writing later. So a dangling edge is only a DEFECT when the target
# plainly exists under a different spelling; those are reported (and optionally repaired)
# separately from the genuine not-yet-written markers.
_TYPE_PREFIXES = ("feedback_", "reference_", "project_", "user_")


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


def _strip_type_prefix(stem: str) -> str:
    for p in _TYPE_PREFIXES:
        if stem.startswith(p):
            return stem[len(p):]
    return stem


def resolve_edge(target: str, stems: set) -> "str | None":
    """The memory a [[link]] plainly MEANS, or None if it is a genuine not-yet-written marker.

    Only unambiguous repairs are returned — the two spellings that actually occur in practice:
    a link written with the .md extension, and a link written with the wrong type prefix
    (reference_ vs feedback_ vs project_). If more than one memory could be meant, we return
    None and report it rather than guess: a link silently repointed at the wrong memory is
    worse than a link that is visibly broken."""
    t = target.strip()
    if t in stems:
        return t
    if t.endswith(".md") and t[:-3] in stems:
        return t[:-3]
    bare = _strip_type_prefix(t[:-3] if t.endswith(".md") else t).replace("-", "_")
    matches = [s for s in stems if _strip_type_prefix(s) == bare]
    return matches[0] if len(matches) == 1 else None


def audit_edges(mem_dir: pathlib.Path) -> dict:
    """Wiki-link edges: which resolve, which are repairable misspellings, which are genuine
    markers for memories worth writing."""
    files = [p for p in mem_dir.glob("*.md") if p.name != INDEX]
    stems = {p.stem for p in files}
    repairable, unwritten, total = {}, set(), 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for target in WIKI_RE.findall(text):
            total += 1
            if target.strip() in stems:
                continue
            fixed = resolve_edge(target, stems)
            if fixed:
                repairable.setdefault(target.strip(), fixed)
            else:
                unwritten.add(target.strip())
    return {"edges": total, "repairable": repairable, "unwritten": sorted(unwritten)}


def fix_edges(mem_dir: pathlib.Path, repairable: dict) -> int:
    """Repoint plainly-misspelled [[links]] at the memory they mean. Only the unambiguous
    ones from resolve_edge ever reach here."""
    if not repairable:
        return 0
    changed = 0
    for p in mem_dir.glob("*.md"):
        if p.name == INDEX:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Rewrite via the SAME regex that found the edge, so the aliased form
        # [[target|display text]] is repaired too. A literal "[[bad]]" replace missed those
        # and then reported a repair it had not made — caught 2026-08-16 on an aliased link;
        # a fix that claims success without effect is the failure class this whole script
        # exists to catch, so it must not be one.
        def _sub(m):
            target = m.group(1).strip()
            good = repairable.get(target)
            if not good:
                return m.group(0)
            alias = m.group(0)[2:-2].split("|", 1)
            return f"[[{good}|{alias[1]}]]" if len(alias) == 2 else f"[[{good}]]"

        new = WIKI_RE.sub(_sub, text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def audit(mem_dir: pathlib.Path) -> dict:
    index_path = mem_dir / INDEX
    tail_path = mem_dir / TAIL_INDEX
    files = sorted(p for p in mem_dir.glob("*.md") if p.name not in INDEX_FILES)
    if not index_path.exists():
        return {"dir": mem_dir, "missing_index": True, "files": len(files),
                "orphans": [p.name for p in files], "dangling": [], "size": 0,
                "split": False, "tail_unreachable": False}

    index_text = index_path.read_text(encoding="utf-8", errors="replace")
    split = tail_path.exists()
    # A memory counts as INDEXED if either index links it — the split is a load-time
    # optimisation, not a reduction in what is discoverable.
    linked = set(LINK_RE.findall(index_text))
    if split:
        linked |= set(LINK_RE.findall(tail_path.read_text(encoding="utf-8", errors="replace")))
    # The failure mode the split INTRODUCES: if the core stops naming the tail file, every
    # entry that lives only in the tail becomes unreachable — silently, and exactly like an
    # orphan. So the pointer is checked, not assumed.
    tail_unreachable = split and TAIL_INDEX not in index_text
    present = {p.name for p in files}

    return {
        "dir": mem_dir,
        "missing_index": False,
        "files": len(files),
        # Exists on disk, nothing points at it -> never loaded, invisible.
        "orphans": sorted(present - linked),
        # Index points at a file that is gone -> a promise the index cannot keep.
        "dangling": sorted(linked - present),
        # Only MEMORY.md is auto-loaded, so only its size is the boot cost being guarded.
        "size": len(index_text.encode("utf-8")),
        "split": split,
        "tail_unreachable": tail_unreachable,
    }


def missing_index_is_lossy(result: dict) -> bool:
    """A missing MEMORY.md is a SILENT-LOSS condition only when there are FILES it would orphan.
    An empty dir (no index, no files) loses nothing — there is nothing to index and nothing to
    forget, so flagging it NO-INDEX is noise the --alert path then pages (bus 38069 false positive
    on the empty quality-audit-drain dir). Nazim 37868: files=0 EMPTY dirs are ok/empty, not
    'needs attention'. Same class as idle-stale-telemetry-is-not-dangerous-blind."""
    return bool(result["missing_index"]) and result["files"] > 0


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


def classify_flags(r: dict, e: dict) -> tuple:
    """Split one dir's audit result into (hard, advisory) flag-string lists.

    HARD = active or imminent SILENT-LOSS (NO-INDEX with real orphans, orphans, dangling links,
    broken edges, an unreachable tail, OVER-READ-LIMIT) -> page the SRE EVERY run until cleared;
    never deduped, because the loss is happening/at-the-cliff and must stay loud.
    ADVISORY = NEAR-LIMIT only (approaching the read-limit, not-yet loss) -> page the dir OWNER,
    deduped. The status line still concatenates hard+advisory, so DETECTION is unchanged."""
    hard, advisory = [], []
    if missing_index_is_lossy(r):
        hard.append("NO-INDEX")
    if r.get("tail_unreachable"):
        hard.append(f"TAIL-UNREACHABLE({TAIL_INDEX} exists but MEMORY.md never names it)")
    if e["repairable"]:
        hard.append(f"BROKEN-EDGES={len(e['repairable'])}")
    if r["orphans"]:
        hard.append(f"ORPHANED={len(r['orphans'])}")
    if r["dangling"]:
        hard.append(f"DANGLING={len(r['dangling'])}")
    if r["size"] >= READ_LIMIT_BYTES:
        hard.append(f"OVER-READ-LIMIT({r['size']}B)")
    elif r["size"] >= WARN_BYTES:
        advisory.append(f"NEAR-LIMIT({r['size']}B, {READ_LIMIT_BYTES - r['size']}B headroom)")
    return hard, advisory


def resolve_owner(label: str, known_agents) -> "str | None":
    """Best-effort OWNER agent for a project memory-dir slug, for ADVISORY (NEAR-LIMIT) routing.
    The slug is the project path with '/'->'-' (e.g. '-Users-me-wingmen-projects-ihsanos'). Strip
    the `<home>-wingmen-` prefix and an optional `projects-` segment, form `cc-<remainder>`, and
    accept it ONLY if it is a real registered agent. Returns None when unresolvable -> the caller
    routes to the SRE (an advisory is never silently dropped because a lookup failed). The whole-
    remainder (not a '-'-split) keeps multi-word projects intact (fleet-health -> cc-fleet-health)."""
    home = str(pathlib.Path.home()).strip("/").replace("/", "-")
    s = label
    for pre in (f"-{home}-wingmen-", f"{home}-wingmen-"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    else:
        return None
    if s.startswith("projects-"):
        s = s[len("projects-"):]
    cand = f"cc-{s}"
    return cand if cand in set(known_agents) else None


def plan_alerts(entries, state: dict, known_agents, sre_agent: str = SRE_AGENT):
    """PURE dedup + routing (Nazim 38293). `entries` = [(label, hard_flags, advisory_flags)] for the
    dirs flagged THIS run; `state` = prior alert memory ({label: {"near_limit": True}}). Returns
    (posts, new_state).

      * HARD flags -> ALWAYS a post to `sre_agent` (never deduped): active/at-cliff loss stays loud.
      * ADVISORY (NEAR-LIMIT) -> a post to the dir OWNER (fallback `sre_agent`), but ONLY if this dir
        was not already advised (dedup). new_state carries the near_limit mark ONLY for dirs still
        advisory this run, so a dir that CLEARED or WORSENED-to-hard drops out and re-arms.

    Load-bearing invariant: dedup touches ONLY the stable-NEAR-LIMIT case. Any hard flag, any
    worsening (NEAR->OVER surfaces as a hard OVER post), and any brand-new advisory all alert."""
    posts, new_state = [], {}
    for label, hard, advisory in entries:
        if hard:
            posts.append({"to": sre_agent, "label": label, "flags": list(hard), "tier": "hard"})
        if advisory:
            already = bool(state.get(label, {}).get("near_limit"))
            new_state[label] = {"near_limit": True}
            if not already:
                owner = resolve_owner(label, known_agents) or sre_agent
                posts.append({"to": owner, "label": label, "flags": list(advisory), "tier": "advisory"})
    return posts, new_state


def _load_alert_state() -> dict:
    try:
        return json.loads(ALERT_STATE_PATH.read_text())
    except Exception:
        return {}  # missing/corrupt -> re-alert once, never silent-suppress-forever


def _save_alert_state(state: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(ALERT_STATE_PATH)  # atomic


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true",
                    help="append index lines for orphaned memory files")
    ap.add_argument("--alert", action="store_true",
                    help="dedup+route bus alerts: NEAR-LIMIT->owner (deduped), hard flags->SRE (every run)")
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

        e = audit_edges(mem_dir)
        if a.fix and e["repairable"]:
            n_files = fix_edges(mem_dir, e["repairable"])
            before = len(e["repairable"])
            e = audit_edges(mem_dir)
            # Report what actually CHANGED, not what was attempted. The re-audit is the
            # evidence; anything still broken stays flagged rather than being counted as done.
            e["fixed"] = (before - len(e["repairable"]), n_files)

        label = mem_dir.parent.name
        hard, advisory = classify_flags(r, e)
        flags = hard + advisory  # DETECTION is unchanged: the status line reports every flag

        status = " ".join(flags) if flags else "ok"
        extra = f" (+{r.get('added')} added)" if r.get("added") else ""
        if e.get("fixed"):
            extra += f" ({e['fixed'][0]} edges repaired in {e['fixed'][1]} files)"
        print(f"{label:<58} files={r['files']:<4} idx={r['size']:<6}B edges={e['edges']:<4} "
              f"{status}{extra}")
        if r["orphans"]:
            for n in r["orphans"][:10]:
                print(f"    orphan: {n}")
        for n in r["dangling"]:
            print(f"    dangling: {n}")
        for bad, good in list(e["repairable"].items())[:10]:
            print(f"    broken edge: [[{bad}]] -> [[{good}]]")
        # NOT a defect: the memory doctrine says a link with no target yet marks something
        # worth writing. Listed so the backlog is visible, never counted as a failure.
        if e["unwritten"]:
            print(f"    unwritten targets (worth writing, not errors): {', '.join(e['unwritten'][:6])}")
        if flags:
            problems.append((label, hard, advisory, r))

    if not problems:
        print("\nALL CLEAN — every memory file is indexed, every link resolves, "
              "every index is under the read limit.")
        return 0

    print(f"\n{len(problems)} agent memory dir(s) need attention.")
    if a.alert:
        _dispatch_alerts(problems)
    return 1


def _hard_body(posts: list, rmap: dict) -> tuple:
    """(subject, body) for the SRE hard/structural page — unchanged silent-loss framing."""
    lines = [
        "Memory-index audit found silent-loss conditions. An ORPHANED file is never loaded at boot "
        "(invisible from birth); an OVER-LIMIT index truncates on read and whatever falls off the "
        "end stops existing, with no error.",
        "",
    ]
    for p in posts:
        lines.append(f"  {p['label']}: {' '.join(p['flags'])}")
        for n in rmap.get(p["label"], {}).get("orphans", [])[:10]:
            lines.append(f"      orphan: {n}")
    lines += [
        "",
        "FIX: `python scripts/memory_index_audit.py --fix` appends missing index lines from each "
        "file's own frontmatter (append-only, reversible). An OVER-LIMIT index needs a manual "
        "compaction pass — group related entries onto one line and trim hooks; never drop a link.",
    ]
    return (f"memory-index audit: {len(posts)} agent memory dir(s) losing entries silently",
            "\n".join(lines))


def _advisory_body(owner: str, posts: list, is_fallback: bool) -> tuple:
    """(subject, body) for a NEAR-LIMIT advisory routed to the dir OWNER (or SRE fallback)."""
    labels = ", ".join(p["label"] for p in posts)
    flags = "; ".join(f"{p['label']}: {' '.join(p['flags'])}" for p in posts)
    note = ("" if not is_fallback else
            "(routed to you as SRE — the dir's owner agent could not be resolved.)\n")
    body = (
        f"TL;DR (advisory, do at a convenient seam — not a page): your memory index is approaching "
        f"the boot read-limit; compact it before it crosses so nothing silently drops at your next "
        f"boot.\n\n{note}"
        f"{flags}\n\n"
        f"HOW (owner compacts own): group related index lines onto ONE line, shorten the one-line "
        f"hooks — NEVER drop a [[link]] or a link target (an orphan gets re-flagged and the index "
        f"regrows). Only the MEMORY.md index text shrinks; the memory files are untouched. This is "
        f"deduped — you won't be re-pinged for the same NEAR-LIMIT until it clears or worsens."
    )
    return (f"Advisory: your memory index is NEAR the boot read-limit ({labels}) — compact at a seam", body)


def _dispatch_alerts(problems: list, connect=None, dsn: "str | None" = None) -> None:
    """Route + dedup the audit's bus alerts (Nazim 38293). HARD/structural flags -> the SRE, every
    run (loud). NEAR-LIMIT advisories -> the dir OWNER (SRE fallback), deduped once per episode via
    persisted state. Never takes down the checker — a dispatch failure only warns."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import psycopg
        from dotenv import load_dotenv
        load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
        dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        connect = connect or psycopg.connect

        rmap = {label: r for (label, hard, advisory, r) in problems}
        entries = [(label, hard, advisory) for (label, hard, advisory, r) in problems]
        state = _load_alert_state()

        with connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT id FROM agents")
                    known_agents = {row[0] for row in cur.fetchall()}
                except Exception:
                    known_agents = set()  # owner unresolvable -> SRE fallback, never crash

            posts, new_state = plan_alerts(entries, state, known_agents)
            if not posts:
                print("all flagged dirs already alerted (deduped) — no new page")
            else:
                by_to = {}
                for p in posts:
                    by_to.setdefault(p["to"], []).append(p)
                with conn.cursor() as cur:
                    for to, ps in by_to.items():
                        hard_ps = [p for p in ps if p["tier"] == "hard"]
                        adv_ps = [p for p in ps if p["tier"] == "advisory"]
                        if hard_ps:  # any hard flag -> the loud SRE page
                            subj, body = _hard_body(hard_ps, rmap)
                            cur.execute(
                                "INSERT INTO agent_messages (from_agent,to_agent,message_type,"
                                "subject,body,priority,requires_response) VALUES "
                                "('orch-console',%s,'question',%s,%s,'P2',true)", (to, subj, body))
                            print(f"posted HARD page to {to}: {subj}")
                        if adv_ps:  # advisory -> owner (or SRE fallback)
                            subj, body = _advisory_body(to, adv_ps, is_fallback=(to == SRE_AGENT))
                            cur.execute(
                                "INSERT INTO agent_messages (from_agent,to_agent,message_type,"
                                "subject,body,priority,requires_response) VALUES "
                                "('cc-fleet-health',%s,'update',%s,%s,'P3',false)", (to, subj, body))
                            print(f"posted ADVISORY to {to}: {subj}")
        _save_alert_state(new_state)
    except Exception as exc:  # noqa: BLE001 — the alarm must never take down the checker
        print(f"WARN: could not dispatch alerts: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
