#!/usr/bin/env python3
"""handoff_verify — check a restore point by CONTENT before anything clears the body.

WHY. Every reset script on this fleet gates on `[ -f "$HANDOFF" ]`. On 2026-08-15 that gate
was tested against reality four times and passed every one it should have failed:

  cc-fleet-health   restore point ~18h old, from before a full day of work
  cai               ~8h old
  cc-irsyad-coord   reported two handoffs on the bus; NEITHER FILE EXISTED
  cc-irsyad-receipt "no further action pending" while its runbook PR sat unmerged

Three of three files checked that night were untrustworthy. A recycle onto a stale restore
point does not preserve the work, it launders the loss: the body comes back confident and
wrong, and nobody can tell what went. That is worse than not recycling.

WHAT A MACHINE CAN AND CANNOT DO, because the boundary is the whole design:

CAN — that the file exists, was written AFTER the checkpoint was asked for, is substantial,
carries the sections that matter, and — the one that actually bites — that its REFERENCES
RESOLVE. These handoffs are dense with checkable claims: file paths, PR numbers, commit
SHAs, bus ids. Coord's phantom handoffs would have died here in milliseconds.

CANNOT — that the contents are TRUE. A body can write a fresh, well-formed, fully-resolving
handoff that is confidently wrong. No checker catches that. The defences are elsewhere and
they are human-shaped: the body writing it did the work (CAI-936, first-hand only, nobody
declares doneness for another lane), and whoever boots next verifies claims at source rather
than believing the file. This module must never be described as making handoffs trustworthy.
It makes them CHECKABLE, which is a smaller and more honest claim.

Reference resolution is injected, so the decision core stays pure and testable and the I/O
(git, gh, the bus) lives at the edge with the caller.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# A restore point below this is a note, not a handoff. Coord's real one was ~7KB; the
# thinnest genuinely useful one seen was ~3.4KB. Deliberately low — this is a floor against
# a one-line "all done", not a quality bar, and length is a terrible proxy for quality.
MIN_BYTES = 800

# What a handoff has to answer, phrased as the QUESTION each section exists to answer. The
# middle one is the reason the module exists: pending state that lives only in a body's head
# is the class a recycle destroys, and it is invisible to every gauge we have. Tonight it was
# an unsent client schedule, a live money-safety monitor, and an unmerged runbook PR.
#
# These patterns were tuned against the six REAL handoffs written on 2026-08-15, and the
# first draft refused three of them — cai, coord and receipt — all of which were good and
# two of which I had already read and accepted by hand. That is the precise failure the
# module warns about elsewhere: a checker that cries wolf gets ignored, and then it is worth
# less than nothing. Two lessons are baked in below.
#
# One: match how bodies actually write, not how a spec imagines they do. coord's section was
# literally "## 2. ONLY-IN-MY-CONTEXT" and the draft pattern required "my " with a SPACE, so
# a single hyphen sank a genuinely excellent handoff.
#
# Two: a STAND-DOWN handoff legitimately has no next step — "this workstream is finished" is
# a complete answer to "what happens next", and refusing it would push retiring lanes to
# invent work. So closure phrasing satisfies next_step.
REQUIRED_SECTIONS = {
    "next_step": (r"next[- ]step|right now|mid-?way|mid-?flight|in[- ]flight|currently|"
                  r"stand[- ]?down|standing down|complete|finished|nothing (further|owed|"
                  r"pending)|bottom line|first on boot|resume"),
    "only_in_context": (r"only[- ]in[- ](my[- ])?context|in[- ]my[- ]context|lives only|"
                        r"exists only|only in (my |a )?head|recycle would destroy|"
                        r"recycle destroys|recycle-destroying|destroyed by a recycle|"
                        r"pending that lives|unsent|promised.{0,25}unsent|re-?launch if"),
    "verified_split": (r"\[VERIFIED\]|\[BUS\]|verified.{0,25}(vs|split|at source)|"
                       r"unverified|first[- ]hand|never on report|not relayed|"
                       r"trust the substrate|verify at source"),
}

_RX = {k: re.compile(v, re.I) for k, v in REQUIRED_SECTIONS.items()}

# Claims that can be mechanically resolved. Anchored to how these bodies actually write.
_REF_PATTERNS = {
    "path": re.compile(r"(?:^|[\s`(])((?:~|/|reports/|scripts/|docs/|migrations/|tests/)[\w./\-]+\.\w{1,6})"),
    "pr": re.compile(r"\bPRs?\s*#(\d{1,6})\b", re.I),
    "sha": re.compile(r"\b([0-9a-f]{7,40})\b"),
    "bus": re.compile(r"(?:bus\s*|#)(\d{4,6})\b", re.I),
}

# A 7-40 char hex run also matches ordinary words like "deadbeef" and, more importantly,
# blob/decision ids that are not commits. Resolution is best-effort by design: an
# unresolvable SHA is reported as UNRESOLVED, never as a failure, because a false alarm here
# would train people to ignore the checker — which costs more than the check is worth.
_SHA_MIN = 7


@dataclass
class HandoffReport:
    ok: bool
    path: str
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    refs: Dict[str, List[str]] = field(default_factory=dict)
    unresolved: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            s = f"PASS {self.path}"
            if self.warnings:
                s += " — warnings: " + "; ".join(self.warnings)
            return s
        return f"REFUSE {self.path} — " + "; ".join(self.failures)


def extract_refs(text: str) -> Dict[str, List[str]]:
    """Mechanically checkable claims in a handoff, deduped, order preserved."""
    out: Dict[str, List[str]] = {}
    for kind, rx in _REF_PATTERNS.items():
        seen, vals = set(), []
        for m in rx.finditer(text):
            v = m.group(1)
            if kind == "sha" and len(v) < _SHA_MIN:
                continue
            if v not in seen:
                seen.add(v)
                vals.append(v)
        out[kind] = vals
    return out


def missing_sections(text: str) -> List[str]:
    return [name for name, rx in _RX.items() if not rx.search(text)]


def verify(
    path: str,
    text: Optional[str],
    mtime: Optional[float] = None,
    requested_at: Optional[float] = None,
    resolver: Optional[Callable[[str, str], Optional[bool]]] = None,
    min_bytes: int = MIN_BYTES,
) -> HandoffReport:
    """Decide whether this restore point is good enough to clear the body on.

    text          file contents, or None if the file does not exist. None is the coord case.
    mtime         file mtime.
    requested_at  when the checkpoint was ASKED for. Freshness is measured against this, not
                  against the clock: "written in the last hour" passes an 18h-stale file if
                  the body happened to touch it, whereas "written since I asked" cannot.
    resolver      (kind, value) -> True resolved / False does not exist / None cannot tell.
                  None is NOT a failure — see _SHA_MIN.

    Refuses on absence, staleness, thinness, missing sections, or a reference that provably
    does not exist. Warns where it genuinely cannot tell, so the caller sees the difference
    between "checked and fine" and "could not check".
    """
    rep = HandoffReport(ok=False, path=path)

    if text is None:
        rep.failures.append("file does not exist — the claim that it was written is false")
        return rep

    if requested_at is not None and mtime is not None and mtime < requested_at:
        age = int(requested_at - mtime)
        rep.failures.append(
            f"stale: written {age}s BEFORE the checkpoint was requested, so it cannot "
            f"describe the state we are about to clear"
        )

    if len(text.encode("utf-8", "replace")) < min_bytes:
        rep.failures.append(f"too thin ({len(text)}B < {min_bytes}B) to be a restore point")

    miss = missing_sections(text)
    if miss:
        rep.failures.append(
            "missing required section(s): " + ", ".join(sorted(miss)) +
            " — the only-in-context section is the one a recycle destroys"
        )

    rep.refs = extract_refs(text)
    if resolver is not None:
        for kind in ("path", "pr", "bus"):
            for v in rep.refs.get(kind, []):
                verdict = resolver(kind, v)
                if verdict is False:
                    rep.failures.append(f"{kind} reference does not exist: {v}")
                elif verdict is None:
                    rep.unresolved.append(f"{kind}:{v}")
        if rep.unresolved:
            rep.warnings.append(
                f"{len(rep.unresolved)} reference(s) could not be checked "
                f"({', '.join(rep.unresolved[:5])}) — not verified, not failed"
            )
    else:
        rep.warnings.append("no resolver supplied — references were NOT checked")

    rep.ok = not rep.failures
    return rep
