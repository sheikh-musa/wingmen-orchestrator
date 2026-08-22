#!/usr/bin/env python3
"""handoff_compaction_policy.py — SSOT for STAGED handoff auto-compaction at recycle time.

Nazim #31825 (item-3 endgame): wire `compact_handoff --apply` into the recycle-checkpoint
flow ENFORCE-IN-CODE — "so nobody has to REMEMBER to run it; a control that needs
remembering is a bug." A handoff already <= cap is a NO-OP, so this is safe to land now and
only fires when a handoff exceeds cap.

STAGING — the ONE knob is COMPACTION_HELD. Advance the rollout by REMOVING a name from it
(after that tier proves clean), never by adding call-sites:
    tier 1  console / nazim              — PROVEN (Nazim ran the first --apply by hand).
    tier 2  coord + engineer/worker lanes + fleet-health — ENABLED here.
    tier 3  cai                          — HELD until one full clean fleet cycle proves it
                                           (governance body, highest stakes; last per order).

CONTRACT (inherited from compact_handoff): SECTION[0] == current-state is kept VERBATIM; the
cap is met by collapsing middle/superseded sections. Every handoff must therefore keep its
current state in section[0] (its header block). compact_handoff enforces the keep-verbatim
half; handoff authors keep the convention.

FAIL LOUD (dead-man's-switch): compaction is a PRE-reset step. compact_handoff_file writes a
timestamped .bak FIRST, then refuses to write an empty-or-larger result (leaving the original
intact). If it RAISES (I/O), we PROPAGATE — the caller MUST abort the recycle rather than
reset onto a possibly half-written restore point (the laundered-loss failure the whole flow
guards against). We never swallow.

Two call-sites, one policy:
  - scripts/self_recycle.sh          (singletons: console/nazim, cai, fleet-health)
  - scripts/sre_lane_recycle.py::armed_recycle  (workers: coord + engineer lanes)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `compact_handoff` (in scripts/, the parent of this lib/) importable whether we are
# run as `-m scripts.lib.handoff_compaction_policy` or imported flat off scripts/lib.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# The ONLY knob. Matched against BOTH the canonical agent id and the tmux session, so 'cai'
# (agent id == session) is held on either path.
COMPACTION_HELD = frozenset({"cai"})


def should_compact(*, agent: "str | None" = None, session: "str | None" = None) -> bool:
    """True iff this body's handoff may be auto-compacted at recycle time. Fail-closed on a
    held name (agent OR session held -> held). Unknown/None identifiers default to ENABLED —
    a no-op under cap makes that safe — but anything in COMPACTION_HELD is always held."""
    for ident in (agent, session):
        if ident and ident in COMPACTION_HELD:
            return False
    return True


def compact_if_enabled(handoff_path: str, *, agent: "str | None" = None,
                       session: "str | None" = None, stamp: str,
                       dry_run: bool = False, cap_bytes: int = 60000) -> dict:
    """Apply staged compaction to a VERIFIED-FRESH handoff.

    Returns compact_handoff_file's summary dict (path/before_bytes/after_bytes/changed/
    wrote/backup...). When this body is HELD, returns {'held': <name>, wrote=False,
    changed=False} and does NOT touch the file. No-op when the handoff is already <= cap.
    Propagates I/O exceptions (the caller aborts the recycle)."""
    if not should_compact(agent=agent, session=session):
        held = agent if (agent and agent in COMPACTION_HELD) else session
        return {"held": held, "path": handoff_path, "wrote": False, "changed": False,
                "before_bytes": None, "after_bytes": None}
    import compact_handoff  # resolved via _SCRIPTS_DIR on sys.path
    return compact_handoff.compact_handoff_file(
        handoff_path, cap_bytes=cap_bytes, dry_run=dry_run, stamp=stamp)


def main(argv: "list[str] | None" = None) -> int:
    """CLI for the bash call-site (self_recycle.sh).

    Usage:
      python -m scripts.lib.handoff_compaction_policy apply \
          --handoff <path> [--agent <id>] [--session <sess>] --stamp <ts> [--dry-run]

    Exit codes are the recycle-abort contract:
      0  compacted, no-op, held, OR the tool fail-closed on its own (original intact — the
         recycle is SAFE to proceed on the untouched handoff; we print a WARN).
      3  an unexpected exception (I/O) — the caller MUST abort the recycle (fail loud).
      2  bad arguments.
    """
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="compact the handoff if this body's tier is enabled")
    a.add_argument("--handoff", required=True)
    a.add_argument("--agent", default=None)
    a.add_argument("--session", default=None)
    a.add_argument("--stamp", required=True)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--cap-bytes", type=int, default=60000)
    ns = ap.parse_args(argv)

    ident = ns.agent or ns.session or "?"
    try:
        r = compact_if_enabled(ns.handoff, agent=ns.agent, session=ns.session,
                               stamp=ns.stamp, dry_run=ns.dry_run, cap_bytes=ns.cap_bytes)
    except Exception as e:  # noqa: BLE001 — fail LOUD, never silent
        print(f"handoff-compaction: FAILED for {ident} on {ns.handoff}: {e!r}",
              file=sys.stderr)
        return 3

    if r.get("held"):
        print(f"handoff-compaction: SKIP (tier held: {r['held']}) — {ns.handoff}")
    elif r.get("error"):
        # The tool declined to write (empty/larger) — original UNTOUCHED, recycle safe.
        print(f"handoff-compaction: WARN — {r['error']} — leaving original intact ({ns.handoff})")
    elif r.get("wrote"):
        print(f"handoff-compaction: compacted {ident} "
              f"{r['before_bytes']}B -> {r['after_bytes']}B (bak: {r.get('backup')})")
    elif r.get("dry_run"):
        verb = "WOULD compact" if r.get("changed") else "no-op (<=cap)"
        print(f"handoff-compaction: dry-run — {verb} for {ident} ({ns.handoff})")
    else:
        print(f"handoff-compaction: no-op (<=cap) for {ident} — {ns.handoff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
