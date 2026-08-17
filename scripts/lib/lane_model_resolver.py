#!/usr/bin/env python3
"""Canonical lane→GROUP-model resolver (#24392: per-GROUP model pointers).

WHY THIS EXISTS
A NEW worker lane with no per-body `.<session>_model` pointer fell straight through
to `.fleet_model` (a fleet-wide Sonnet flip for token conservation) instead of its
FAMILY's model — a SILENT capability downgrade that only bites new lanes (existing
lanes have per-body pointer files, so the fleet looks consistent) at the same
billing. This mirrors the TOKEN path, which already resolves a per-GROUP tier
(scripts/lib/lane_token_resolver `.group_default_token.<family>`) — which is why the
same boot came out token-right but model-wrong.

THE FIX inserts a per-GROUP model tier `.group_default_model.<family>` BETWEEN the
`.<session>_model` tier and `.fleet_model`, so the boot precedence becomes:

  MODEL env > .<session>_model > .group_default_model.<family> > .fleet_model > opus-4-8

This module owns ONLY the group tier (tier 3). The caller
(scripts/launch_dangerous_cc.sh) keeps handling MODEL env, `.<session>_model` and
`.fleet_model` AROUND it, exactly as before — so with NO group file present the
resolution is byte-identical to the pre-fix behaviour.

FAMILY DERIVATION IS SHARED, NOT DUPLICATED: `family_of` is re-exported from
lane_token_resolver so `.group_default_model.<family>` keys off the IDENTICAL family
as `.group_default_token.<family>` (a twin family-map would drift — the twin-drift
lesson). The non-worker body classes (`_SESSION_POINTER`, `_NO_POINTER_SINGLETONS`)
are reused for the same reason: nazim / cc-orchestrator / cai / fleet-health are
env- or per-body-model driven and never use a group tier.

Unlike the token resolver, a group MODEL file stores the model id DIRECTLY (not a
pointer to another file), and there is no fingerprint / forbidden-account concept.

Fail-OPEN: a missing / unreadable / empty / whitespace-only group file returns None
(the caller falls through to `.fleet_model`); it NEVER raises.
"""
from __future__ import annotations

import os
from typing import Optional

# Reuse — do NOT reimplement — the family derivation and body classification so the
# model group tier stays byte-consistent with the token group tier.
from scripts.lib.lane_token_resolver import (
    family_of,
    _NO_POINTER_SINGLETONS,
    _SESSION_POINTER,
)

# The per-group MODEL file prefix. The family is appended:
# `.group_default_model.irsyad`, `.group_default_model.cosem`, …
_GROUP_MODEL_PREFIX = ".group_default_model"


def _default_orch_dir() -> str:
    """The orchestrator dir = the repo root this module lives in (scripts/lib/x.py
    -> parents[2]), the same tree the `.group_default_model.*` files live in."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_lane_group_model(session: str, orch_dir: Optional[str] = None) -> Optional[str]:
    """Resolve `session` -> the model id from `.group_default_model.<family>`, or
    None to fall through to the caller's `.fleet_model` tier.

    Worker lanes only: per-session bodies (nazim / cc-orchestrator) and no-pointer
    singletons (cai / fleet-health) are env/per-body-model driven and always return
    None. Fail-open on any missing / unreadable / empty file.
    """
    if not session:
        return None
    # Non-worker bodies never consult the group tier (mirrors the token resolver).
    if session in _SESSION_POINTER or session in _NO_POINTER_SINGLETONS:
        return None
    orch_dir = orch_dir or _default_orch_dir()
    fam = family_of(session)
    if not fam:
        return None
    try:
        with open(os.path.join(orch_dir, "%s.%s" % (_GROUP_MODEL_PREFIX, fam))) as f:
            model = f.read().strip()
        return model or None
    except Exception:
        return None


def _main(argv=None) -> int:
    """CLI shim for launch_dangerous_cc.sh: print the resolved model id (or NOTHING
    for a None resolution) and exit 0. Prints ONLY the model on stdout — no log
    noise — so the caller can `$(...)` it directly."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Resolve a lane's per-group model (#24392).")
    ap.add_argument("--session", default="", help="tmux session / lane name (e.g. irsyad-coord)")
    ap.add_argument("--orch-dir", default=None, help="orchestrator dir (defaults to this repo root)")
    args = ap.parse_args(argv)
    try:
        model = resolve_lane_group_model(args.session, orch_dir=args.orch_dir)
    except Exception:
        # Fail-open at the boundary too: never let a resolver error break a launch.
        model = None
    if model:
        sys.stdout.write(model + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(_main())
