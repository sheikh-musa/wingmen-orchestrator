#!/usr/bin/env python3
"""Canonical lane→token-key-file resolver (GAP-B: per-GROUP token pointers).

THE CORRECTNESS CRUX (why this module exists):
The account a lane ACTUALLY BOOTS ON (resolved in scripts/launch_dangerous_cc.sh)
and the account the console shows as "expected" (nervous_system/console/panes.py
`_expected_fp`) MUST use the IDENTICAL precedence — otherwise the console can show
"expected musa2" while a lane actually booted on musa (a false-green / wrong-billing
display-vs-boot divergence). bash and python cannot share a function literally, so
BOTH consult THIS one module: panes.py imports `resolve_lane_token_path`, and
launch_dangerous_cc.sh calls the `__main__` CLI shim below (`python3 -m
scripts.lib.lane_token_resolver --session <s>`). Same code → expected==boot BY
CONSTRUCTION.

Precedence per worker lane (HIGH→LOW). This module owns the POINTER tiers (2-4);
the raw env override (tier 1) and the .env fallback (tier 5) are handled by each
caller AROUND this resolver, exactly as before GAP-B:

  1. explicit per-lane override  (env CLAUDE_CODE_OAUTH_TOKEN_OVERRIDE)   ← caller
  2. per-SESSION pointer         (nazim→.nazim_default_token,
                                  cc-orchestrator→.orch_default_token)    ← here
  3. per-GROUP pointer  (NEW)    (.group_default_token.<family>)          ← here
  4. fleet default               (.lane_default_token)                    ← here
  5. .env CLAUDE_CODE_OAUTH_TOKEN                                         ← caller

`resolve_lane_token_path(session)` returns an ABSOLUTE PATH to a token key file, or
None to mean "no pointer resolved — fall through to the .env default account". A
None return is the SAME signal the pre-GAP-B code used, so with NO group file the
resolution is byte-identical to the old behaviour (proven in the tests).

Fail-OPEN: a missing / unreadable / empty group pointer file falls through to the
fleet default — it NEVER raises. Forbidden-fp REFUSAL: a group pointer whose target
token fingerprints to a forbidden account (the gazzabyte consumer Max, fp
13589de86f29, CAI-729) is NOT honored — it falls through to the fleet default, so a
group pin can never boot a lane onto a forbidden token.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

# The gazzabyte consumer Max token is FORBIDDEN fleet-wide (CAI-729). A group
# pointer resolving to this fingerprint is refused (falls through). Kept in sync
# with app.py `_FORBIDDEN_TOKEN_FPS` / panes.py — same short sha256[:12] scheme.
_FORBIDDEN_TOKEN_FPS = {"13589de86f29"}

# Bodies with their OWN per-session token pointer (they are NOT worker lanes, so
# the group + fleet-default tiers do not apply to them). Mirrors panes.py
# `_BODY_POINTER` and app.py `_TOKEN_POINTER_FOR`.
_SESSION_POINTER = {"nazim": ".nazim_default_token", "cc-orchestrator": ".orch_default_token"}

# Singletons that boot straight off the .env account (no pointer of any tier).
# Mirrors panes.py `_NO_POINTER_SINGLETONS` / app.py `_NO_TOKEN_POINTER`.
_NO_POINTER_SINGLETONS = {"cai", "fleet-health"}

# The fleet-wide default pointer every worker lane falls back to (tier 4).
_FLEET_DEFAULT_POINTER = ".lane_default_token"

# The per-group pointer file prefix (tier 3). The family is appended:
# `.group_default_token.irsyad`, `.group_default_token.cosem`, …
_GROUP_POINTER_PREFIX = ".group_default_token"


def _default_orch_dir() -> str:
    """The orchestrator dir = the repo root this module lives in (scripts/lib/x.py
    -> parents[2]). Same tree the pointer files ($ORCH_DIR/.*_default_token) live
    in; panes.py passes its own equivalent `_ORCH_DIR` explicitly."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fingerprint(token: str) -> str:
    """Stable non-reversible short id for an OAuth token (sha256 prefix, 12 hex).
    Identical scheme to panes._fingerprint / app._fp_of_token_file — the raw token
    is never surfaced."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def family_of(session: str) -> Optional[str]:
    """The lane-FAMILY of a session = its prefix up to the first '-', after a
    tolerant strip of the universal `cc-` body prefix (so both a bare
    `irsyad-coord` and a `cc-irsyad-1` map to family `irsyad`). None for an empty
    session or one that reduces to nothing. Segment-based, never a bare substring —
    matches the codebase's own family convention (app._is_in_family)."""
    if not session:
        return None
    s = session[3:] if session.startswith("cc-") else session
    if not s:
        return None
    fam = s.split("-", 1)[0]
    return fam or None


def _read_pointer_target(orch_dir: str, name: str) -> Optional[str]:
    """The token-file PATH a pointer file points at (single line, stripped), or
    None on ANY failure (missing / unreadable / empty). Never raises."""
    try:
        with open(os.path.join(orch_dir, name), "r") as f:
            target = f.read().strip()
        return target or None
    except Exception:
        return None


def _token_fp_at(path: str) -> Optional[str]:
    """Fingerprint the token stored at `path` (the raw token stays local, never
    returned). None if the file is missing / unreadable / empty."""
    try:
        with open(os.path.expanduser(path.strip()), "r") as f:
            tok = f.read().strip()
        return _fingerprint(tok) if tok else None
    except Exception:
        return None


def resolve_lane_token_path(session: str, orch_dir: Optional[str] = None) -> Optional[str]:
    """Resolve `session` -> ABSOLUTE PATH of the token key file it is CONFIGURED to
    boot on, honoring tiers 2 (per-session) → 3 (per-group) → 4 (fleet default), or
    None to fall through to the .env default account (tier 5).

    Fail-open + forbidden-fp-safe (see module docstring). With NO group pointer
    file present the result is byte-identical to the pre-GAP-B resolution.
    """
    orch_dir = orch_dir or _default_orch_dir()

    # Tier 2 — per-SESSION pointer (nazim / cc-orchestrator). These bodies have no
    # group/fleet fallback: an unreadable session pointer -> None (.env), exactly
    # as panes._expected_fp behaved pre-GAP-B.
    sp = _SESSION_POINTER.get(session)
    if sp is not None:
        return _read_pointer_target(orch_dir, sp)

    # Singletons boot off .env — no pointer of any tier.
    if session in _NO_POINTER_SINGLETONS:
        return None

    # Worker lane — Tier 3 (per-GROUP) then Tier 4 (fleet default).
    fam = family_of(session)
    if fam:
        group_target = _read_pointer_target(orch_dir, "%s.%s" % (_GROUP_POINTER_PREFIX, fam))
        if group_target:
            fp = _token_fp_at(group_target)
            # Honor the group pin ONLY if its target is a readable, non-forbidden
            # token. An unreadable/empty target OR a forbidden fp -> fall through to
            # the fleet default (fail-open + refusal), never the .env floor.
            if fp is not None and fp not in _FORBIDDEN_TOKEN_FPS:
                return group_target

    # Tier 4 — fleet default (returned as-is; the caller's readability guard is the
    # tier-5 .env fallback, byte-identical to the pre-GAP-B fleet-default handling).
    return _read_pointer_target(orch_dir, _FLEET_DEFAULT_POINTER)


def _main(argv=None) -> int:
    """CLI shim for launch_dangerous_cc.sh: print the resolved token key-file path
    (or NOTHING for a None resolution) and exit 0. Prints ONLY the path on stdout —
    no log noise — so the caller can `$(...)` it directly."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Resolve a lane's configured token key file path (GAP-B).")
    ap.add_argument("--session", default="", help="tmux session / lane name (e.g. irsyad-coord)")
    ap.add_argument("--orch-dir", default=None, help="orchestrator dir (defaults to this repo root)")
    args = ap.parse_args(argv)
    try:
        path = resolve_lane_token_path(args.session, orch_dir=args.orch_dir)
    except Exception:
        # Fail-open at the boundary too: never let a resolver error break a launch.
        path = None
    if path:
        sys.stdout.write(path + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(_main())
