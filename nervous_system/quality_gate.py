"""Quality Gate — the Head of Quality's pre-merge/pre-deploy evaluator (Phase 2).

Head of Quality, Phase 2 (SHADOW-first). See reports/head-of-quality-spec.md §8.

Phase 1 (`ihsan_gate.py`) turned the ihsan bar into queryable data. Phase 2 adds
the *evaluator*: given a CHANGE CLASS and the evidence a caller has gathered for
a diff, it resolves the class, scores each required gate item, and returns a
SHA-scoped verdict — `{would_block, failures, unproven, advisories}`.

SHADOW-FIRST CONTRACT (this phase):
  - The evaluator is a PURE function. It reads the manifest + the evidence dict
    and returns a verdict. It does NOT run checks, touch git, merge, deploy, or
    write anything. Evidence-gathering + persistence live in the (future) wrapper.
  - `QUALITY_GATE_MODE` decides ENFORCEMENT, not computation:
        off    -> compute a verdict but mark it un-enforced (observation only)
        shadow -> compute + (caller logs/persists) + NEVER block   [default]
        block  -> a would_block verdict is enforced (caller must refuse)
    Shipping shadow-first lets thresholds prove out against real traffic before
    anything blocks — the same "passive first" de-risking as the CoS triage layer.

BLOCKING MODEL (spec §8, 267-H1 preserved):
  - deterministic item  -> BLOCKING. A failed check fails the item; a MISSING
    check is `unproven` (dead-man default: absence of proof is never a pass, so
    it counts toward would_block — fail-strict).
  - judgment item       -> ADVISORY taste, NEVER auto-blocks (267-H1)... EXCEPT
    when the item is in the class's `mandatory_reviews`: then its reviewer arm is
    REQUIRED, and a missing/failed review is a blocking gap.

This module has no DB or network dependency; it is unit-testable in isolation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from nervous_system import ihsan_gate

# Enforcement modes.
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_BLOCK = "block"
_VALID_MODES = frozenset({MODE_OFF, MODE_SHADOW, MODE_BLOCK})

# Per-item / per-check outcome labels.
PASS = "pass"
FAIL = "fail"
UNPROVEN = "unproven"      # deterministic item with missing evidence (fail-strict)
ADVISORY = "advisory"      # judgment taste concern (never blocks)


def resolve_mode(mode: Optional[str] = None) -> str:
    """Resolve the enforcement mode: explicit arg > QUALITY_GATE_MODE env >
    'shadow' (the safe default — compute + log, never block)."""
    m = (mode or os.environ.get("QUALITY_GATE_MODE") or MODE_SHADOW).strip().lower()
    return m if m in _VALID_MODES else MODE_SHADOW


def mandatory_reviews_for(
    change_class: str,
    manifest: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> list:
    """The gate-item IDs whose *reviewer arm* is mandatory for this class
    (e.g. money-payment -> ['G5']). Unknown class -> the strictest default's."""
    m = ihsan_gate._manifest(manifest, path)
    key = ihsan_gate.resolve_class(change_class, m)
    spec = m.get("change_classes", {}).get(key, {}) or {}
    return list(spec.get("mandatory_reviews", []))


@dataclass
class ItemVerdict:
    """The outcome for one required gate item."""
    id: str
    title: str
    type: str                    # deterministic | judgment
    status: str                  # PASS | FAIL | UNPROVEN | ADVISORY
    blocking: bool               # does this item's status count toward would_block?
    reason: str = ""
    checks: dict = field(default_factory=dict)   # {check_name: status}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "blocking": self.blocking,
            "reason": self.reason,
            "checks": dict(self.checks),
        }


@dataclass
class GateVerdict:
    """The gate's verdict for one diff. `enforced` is True only in block mode
    with a would_block verdict — shadow/off never enforce."""
    sha: str
    change_class: str
    mode: str
    would_block: bool
    enforced: bool
    items: list = field(default_factory=list)         # list[ItemVerdict]
    failures: list = field(default_factory=list)      # blocking items that FAILED
    unproven: list = field(default_factory=list)      # blocking items UNPROVEN
    advisories: list = field(default_factory=list)    # judgment taste concerns

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "change_class": self.change_class,
            "mode": self.mode,
            "would_block": self.would_block,
            "enforced": self.enforced,
            "items": [i.to_dict() for i in self.items],
            "failures": [i.to_dict() for i in self.failures],
            "unproven": [i.to_dict() for i in self.unproven],
            "advisories": [i.to_dict() for i in self.advisories],
        }


def _score_deterministic(item: dict, check_results: dict) -> ItemVerdict:
    """Deterministic item: a failed check fails the item; a missing check is
    UNPROVEN (fail-strict). All present and none failing -> PASS."""
    iid = item["id"]
    checks = {}
    any_fail = any_missing = False
    for c in item.get("checks", []):
        raw = check_results.get(c)
        if raw is None:
            checks[c] = UNPROVEN
            any_missing = True
        elif str(raw).lower() in ("fail", "false", "red", "error"):
            checks[c] = FAIL
            any_fail = True
        else:                                   # pass / skip / na / true -> not a fail
            checks[c] = PASS
    if any_fail:
        status, reason = FAIL, "a deterministic check failed"
    elif any_missing:
        status, reason = UNPROVEN, "missing evidence for a deterministic floor check"
    else:
        status, reason = PASS, ""
    return ItemVerdict(iid, item.get("title", iid), "deterministic",
                       status, blocking=True, reason=reason, checks=checks)


def _score_judgment(item: dict, check_results: dict, review_present: Optional[bool],
                    mandatory: bool) -> ItemVerdict:
    """Judgment item: taste is advisory (never blocks, 267-H1). If the item is a
    mandatory review for the class, its reviewer arm is required — a missing or
    false review is a blocking FAIL."""
    iid = item["id"]
    checks = {}
    taste_concern = False
    for c in item.get("checks", []):
        raw = check_results.get(c)
        if raw is None:
            checks[c] = UNPROVEN
        elif str(raw).lower() in ("fail", "false", "red", "error"):
            checks[c] = FAIL
            taste_concern = True
        else:
            checks[c] = PASS

    if mandatory and review_present is not True:
        return ItemVerdict(iid, item.get("title", iid), "judgment",
                           FAIL, blocking=True,
                           reason="mandatory reviewer arm absent for this class",
                           checks=checks)
    status = ADVISORY if taste_concern else PASS
    reason = "advisory taste concern (267-H1: never auto-blocks)" if taste_concern else ""
    return ItemVerdict(iid, item.get("title", iid), "judgment",
                       status, blocking=False, reason=reason, checks=checks)


def evaluate(
    change_class: str,
    evidence: dict,
    *,
    mode: Optional[str] = None,
    manifest: Optional[dict] = None,
    path: Optional[str] = None,
) -> GateVerdict:
    """Evaluate the gate for a diff. PURE — does not mutate `evidence`.

    evidence: {
        "sha":     str,                       # the diff/HEAD SHA being gated
        "checks":  {check_name: "pass"|"fail"|"skip"},   # missing -> unproven
        "reviews": {item_id: bool},           # reviewer arm present for an item
    }
    Unknown/ambiguous class -> the strictest default (never under-gate).
    """
    m = ihsan_gate._manifest(manifest, path)
    resolved = ihsan_gate.resolve_class(change_class, m)
    mode_r = resolve_mode(mode)

    check_results = dict((evidence or {}).get("checks", {}))
    reviews = dict((evidence or {}).get("reviews", {}))
    sha = (evidence or {}).get("sha", "")
    mandatory = set(mandatory_reviews_for(resolved, m))

    items: list = []
    for item in ihsan_gate.gate_items_for(resolved, m):
        if item.get("type") == "deterministic":
            items.append(_score_deterministic(item, check_results))
        else:
            items.append(_score_judgment(item, check_results,
                                         reviews.get(item["id"]),
                                         item["id"] in mandatory))

    failures = [i for i in items if i.blocking and i.status == FAIL]
    unproven = [i for i in items if i.blocking and i.status == UNPROVEN]
    advisories = [i for i in items if i.status == ADVISORY]
    would_block = bool(failures) or bool(unproven)
    enforced = (mode_r == MODE_BLOCK) and would_block

    return GateVerdict(
        sha=sha,
        change_class=resolved,
        mode=mode_r,
        would_block=would_block,
        enforced=enforced,
        items=items,
        failures=failures,
        unproven=unproven,
        advisories=advisories,
    )


def should_block(verdict: GateVerdict) -> bool:
    """Whether the caller (a merge/deploy wrapper) must REFUSE. True only when
    the verdict is enforced — i.e. block mode + would_block. Shadow/off: False."""
    return bool(verdict.enforced)


def main(argv=None, *, stdin=None, stdout=None) -> int:
    """CLI seam for a merge/deploy wrapper: read an evidence JSON on stdin,
    evaluate it against a change class, print the verdict JSON, and return an
    exit code that is NON-ZERO only when the verdict is ENFORCED (block mode +
    would_block). Shadow/off always exit 0 — a shadow gate can never break a ship.

    Usage:  quality_gate.py --class <change-class> [--mode off|shadow|block] < evidence.json
    """
    import argparse
    import json
    import sys

    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    parser = argparse.ArgumentParser(prog="quality_gate", description=(
        "Head of Quality shadow gate — score a diff's evidence against the "
        "ihsan bar. Shadow mode logs a would-block verdict but exits 0."))
    parser.add_argument("--class", dest="change_class", required=True,
                        help="the change class (or alias); unknown -> strictest default")
    parser.add_argument("--mode", dest="mode", default=None,
                        help="off|shadow|block (default: QUALITY_GATE_MODE env, else shadow)")
    args = parser.parse_args(argv)

    try:
        evidence = json.load(stdin) or {}
    except (ValueError, TypeError):
        evidence = {}

    verdict = evaluate(args.change_class, evidence, mode=args.mode)
    stdout.write(json.dumps(verdict.to_dict(), indent=2) + "\n")
    # Fail the caller ONLY when the gate is actually enforcing. Shadow -> 0.
    return 1 if should_block(verdict) else 0


if __name__ == "__main__":   # pragma: no cover
    import sys
    sys.exit(main())
