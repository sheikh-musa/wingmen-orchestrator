#!/usr/bin/env python3
"""compact_handoff.py — handoff-hygiene compaction (Nazim #31753, op-approved).

An append-forever handoff (coord's grew to ~697KB) becomes too large for a fresh body to
read whole, defeating its purpose (a handoff a fresh body can't read is worse than none).
This collapses it to a size a fresh body can read, WITHOUT losing the authoritative
current state. RECOVERY-CRITICAL — a handoff is what a recycled body boots from.

KEEPS VERBATIM:
  - the title/preamble (everything before the first '## ' section header)
  - SECTION[0] — the current/identity block in BOTH fleet conventions (nazim:
    section[0] = FINAL STATE; coord: section[0] = identity + operator directives).
    ALWAYS kept (recovery-critical: cc-quality FINDING-1 — a non-keyword top/middle
    current-state block must never be collapsed).
  - the LAST `keep_recent` sections (the coord convention — recent DELTAs are live state)
COLLAPSES every other (middle/superseded) section — INCLUDING earlier/superseded
FINAL-STATE blocks (cc-quality FINDING-2) — into run-summary pointers. The cap is met by
shrinking keep_recent, so a many-FINAL-STATE handoff (nazim's) actually gets under cap.

SAFETY:
  - idempotent: a handoff already <= cap is returned UNCHANGED; re-running on the output
    is a no-op.
  - the cap YIELDS to safety: kept-verbatim current state is NEVER truncated to hit the
    cap (a latest section bigger than the cap is kept whole).
  - no '## ' structure => returned unchanged (never blind-truncate an unstructured file).
  - the FILE wrapper is DRY-RUN by default, writes a timestamped .bak before overwriting,
    and refuses to write an empty/º-shrunk-to-nothing result.
"""
from __future__ import annotations

import re

_SECTION_RE = re.compile(r"^## ", re.M)


def _split_sections(text: str):
    """(preamble, [sections]); each section spans a '## ' header line to the next."""
    idxs = [m.start() for m in _SECTION_RE.finditer(text)]
    if not idxs:
        return text, []
    preamble = text[: idxs[0]]
    sections = []
    for i, start in enumerate(idxs):
        end = idxs[i + 1] if i + 1 < len(idxs) else len(text)
        sections.append(text[start:end])
    return preamble, sections


def _header_line(section: str) -> str:
    return section.splitlines()[0] if section else ""


def _pointer(section: str) -> str:
    hdr = _header_line(section).rstrip()
    return f"- [collapsed] {hdr} ({section.count(chr(10))} lines, {len(section.encode('utf-8'))}B)\n"


def _run_pointer(run: "list[str]") -> str:
    """Collapse a RUN of consecutive superseded sections to ONE line. One-per-section
    pointers don't scale (coord had 464 sections => ~83KB of pointers alone); a run
    summary (count + first…last header + total bytes) keeps the collapse itself tiny."""
    if len(run) == 1:
        return _pointer(run[0])
    first = _header_line(run[0]).rstrip().lstrip("# ")[:56]
    last = _header_line(run[-1]).rstrip().lstrip("# ")[:56]
    tot = sum(len(s.encode("utf-8")) for s in run)
    lines = sum(s.count("\n") for s in run)
    return f"- [collapsed {len(run)} superseded sections: “{first}” … “{last}”] ({lines} lines, {tot}B)\n"


def compact_handoff(text: str, cap_bytes: int = 60000, keep_recent: int = 8) -> str:
    """Compact to <= cap_bytes where safely possible; never lose current state. Idempotent."""
    if len(text.encode("utf-8")) <= cap_bytes:
        return text
    preamble, sections = _split_sections(text)
    if not sections:
        return text  # no section structure — do NOT blind-truncate a recovery file

    n = len(sections)

    def build(kr: int) -> str:
        # ALWAYS keep section[0] verbatim — it is the current/identity block in BOTH
        # fleet conventions (nazim: section[0] = FINAL STATE; coord: section[0] = identity
        # + operator directives). cc-quality FINDING-1 (recovery data-loss): a non-keyword
        # top/middle current-state block was being collapsed once the handoff grew past
        # cap — keeping section[0] closes it. Older superseded blocks (incl earlier
        # FINAL-STATE blocks — cc-quality FINDING-2) collapse to pointers; the cap is met
        # by shrinking keep_recent, so a many-FINAL-STATE handoff (nazim's) gets under cap.
        keep = {0} | set(range(max(0, n - kr), n))
        parts = []
        if preamble.strip():
            parts.append(preamble if preamble.endswith("\n") else preamble + "\n")
        run: "list[str]" = []
        for i, s in enumerate(sections):
            if i in keep:
                if run:
                    parts.append(_run_pointer(run))
                    run = []
                parts.append(s if s.endswith("\n") else s + "\n")
            else:
                run.append(s)
        if run:
            parts.append(_run_pointer(run))
        return "".join(parts)

    kr = max(1, keep_recent)
    out = build(kr)
    while len(out.encode("utf-8")) > cap_bytes and kr > 1:
        kr -= 1
        out = build(kr)
    return out


# ── file wrapper: DRY-RUN by default, backup before write, refuse to lose content ──
def compact_handoff_file(path: str, cap_bytes: int = 60000, keep_recent: int = 8,
                         dry_run: bool = True, stamp: "str | None" = None) -> dict:
    """Compact the handoff at `path`. DRY-RUN by default (returns the summary, writes
    nothing). When dry_run=False: writes `<path>.<stamp>.bak` first, then the compacted
    file. Refuses to write if the result is empty or somehow LARGER than the original.
    `stamp` (a caller-supplied timestamp string) names the backup — pass one (this module
    takes no clock) or a numeric fallback is used."""
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()
    compacted = compact_handoff(original, cap_bytes=cap_bytes, keep_recent=keep_recent)
    ob, cb = len(original.encode("utf-8")), len(compacted.encode("utf-8"))
    summary = {"path": path, "before_bytes": ob, "after_bytes": cb,
               "changed": compacted != original, "dry_run": dry_run, "wrote": False, "backup": None}
    if compacted == original:
        return summary                      # no-op (already within cap / no structure)
    if not compacted.strip() or cb > ob:
        summary["error"] = "refused: result empty or larger than original"
        return summary                      # fail-closed: never write a worse file
    if dry_run:
        return summary
    bak = f"{path}.{stamp or ('bak' + str(ob))}.bak"
    with open(bak, "w", encoding="utf-8") as f:
        f.write(original)
    # ATOMIC overwrite (cc-quality F3, now load-bearing since this auto-fires at recycle):
    # write the compacted body to a temp file in the SAME directory, then os.replace() it over
    # the live handoff. os.replace is atomic on POSIX, so a crash mid-write leaves the live
    # handoff as EITHER the old bytes OR the full new bytes — never a half-written restore
    # point. (.bak already holds the original; this removes the last torn-write window.)
    import os as _os
    import tempfile as _tempfile
    d = _os.path.dirname(path) or "."
    fd, tmp = _tempfile.mkstemp(dir=d, prefix=".compact_", suffix=".tmp")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(compacted)
        _os.replace(tmp, path)
    except BaseException:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
        raise
    summary["wrote"] = True
    summary["backup"] = bak
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Compact an append-forever handoff (dry-run by default).")
    ap.add_argument("path")
    ap.add_argument("--cap-bytes", type=int, default=60000)
    ap.add_argument("--keep-recent", type=int, default=8)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run). Writes a .bak first.")
    ap.add_argument("--stamp", default=None, help="timestamp string for the .bak name")
    a = ap.parse_args()
    s = compact_handoff_file(a.path, cap_bytes=a.cap_bytes, keep_recent=a.keep_recent,
                             dry_run=not a.apply, stamp=a.stamp)
    print(s)
