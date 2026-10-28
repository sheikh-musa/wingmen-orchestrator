#!/usr/bin/env python3
"""Canonical lane→token-key-file resolver (GAP-B: per-GROUP token pointers).

THE CORRECTNESS CRUX (why this module exists):
The account a lane ACTUALLY BOOTS ON (resolved in scripts/launch_dangerous_cc.sh)
and the account the console shows as "expected" (nervous_system/console/panes.py
`_expected_fp`) MUST use the IDENTICAL precedence — otherwise the console can show
"expected irsyad" while a lane actually booted on musa (a false-green / wrong-billing
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

MUSA-PERSONAL FREEZE (operator directive Musa op#28857): while `$ORCH_DIR/
.musa_personal_freeze` is active, a WORKER LANE that would boot on the Musa-personal
OAuth token is redirected to Syed (fail-closed if no valid Syed substitute — it never
silently returns Musa). The freeze is liftable (delete the flag) + auto-expiring (an
ISO-8601 UTC expiry line); with NO flag the resolution is byte-identical to before.
See `_musa_freeze_active` / `_apply_musa_freeze`.
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Optional

# The gazzabyte consumer Max token is FORBIDDEN fleet-wide (CAI-729). A group
# pointer resolving to this fingerprint is refused (falls through). Kept in sync
# with app.py `_FORBIDDEN_TOKEN_FPS` / panes.py — same short sha256[:12] scheme.
_FORBIDDEN_TOKEN_FPS = {"13589de86f29"}

# ── Musa-personal-pool FREEZE guard (operator directive Musa op#28857) ────────
# While a freeze is ACTIVE, NO worker lane may boot on the Musa-PERSONAL OAuth
# token: a lane that would resolve to Musa-personal is redirected to Syed (loudly
# logged), and we NEVER silently return Musa. The freeze is liftable + auto-
# expiring (see `_musa_freeze_active`). With NO flag file present the resolution
# is byte-identical to the pre-op#28857 behaviour (fail-OPEN on the flag's absence).
_MUSA_FREEZE_FLAG = ".musa_personal_freeze"   # $ORCH_DIR/<this> — one line: ISO-8601 UTC expiry
_MUSA_ACCOUNT = "musa"                        # the Musa-PERSONAL account (token_fps.map `musa`)
_SYED_ACCOUNT = "syed"                        # the donor the freeze substitutes in
_SYED_TOKEN_FILENAME = "syed-oauth-token"     # convention: <keys_dir>/<account>-oauth-token

# Sentinel returned when the freeze is active, a lane WOULD boot on Musa-personal,
# and NO valid Syed substitute can be found. It is NEVER a real path and NEVER
# None — because None means "fall through to the .env default", and the .env
# default IS Musa-personal, i.e. exactly what the freeze forbids. The CLI shim
# turns this sentinel into an empty stdout + a distinct non-zero exit (42) so the
# launcher can ABORT the boot instead of booting on the .env Musa account.
_MUSA_FREEZE_REFUSE = "__MUSA_PERSONAL_FREEZE_REFUSE__"

# token_fps.map is the ONE place updated after an OAuth rotation (orch-console
# #49107); TOKEN_FPS_MAP env overrides, exactly like token_file_guard.{sh,py}.
_DEFAULT_FP_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_fps.map")

# Bodies with their OWN per-session token pointer (they are NOT worker lanes, so
# the group + fleet-default tiers do not apply to them). Mirrors panes.py
# `_BODY_POINTER` and app.py `_TOKEN_POINTER_FOR`.
_SESSION_POINTER = {"nazim": ".nazim_default_token", "cc-orchestrator": ".orch_default_token"}

# Singletons with no GROUP/fleet-default tier (they are not worker lanes).
# Mirrors panes.py `_NO_POINTER_SINGLETONS` / app.py `_NO_TOKEN_POINTER`.
_NO_POINTER_SINGLETONS = {"cai", "fleet-health"}

# Each _NO_POINTER_SINGLETONS body DOES still have its own bespoke per-body
# pointer file -- read directly in bash by boot_cai.sh / boot_fleet_health.sh,
# NOT by this module (so it has zero effect on how they actually boot). This
# module previously returned a bare None for them, which fed straight into
# panes._expected_fp's .env fallback and made the console show "expected=Musa"
# for a body that has ACTUALLY been pointer-pinned to Syed for a while (bus
# #51982/#46625, the console's "3 remaining mismatches" P3). Resolving it here
# too -- read-only, same fail-open shape as _SESSION_POINTER -- fixes the
# DISPLAY by construction, via the one module both boot and console consult.
_SINGLETON_OWN_POINTER = {"cai": ".cai_default_token", "fleet-health": ".fleet-health_default_token"}

# The fleet-wide default pointer every worker lane falls back to (tier 4).
_FLEET_DEFAULT_POINTER = ".lane_default_token"

# Per-SESSION pointer for ONE worker lane (tier 2b, console #80254): pins a single
# lane ahead of its group pin without moving its siblings, e.g.
# `.lane_session_token.wingmen-sites`. Same honor rule as the group pin (readable,
# non-forbidden target, else fall through), and still subject to the Musa freeze.
_LANE_SESSION_POINTER_PREFIX = ".lane_session_token"

# The per-group pointer file prefix (tier 3). The family is appended:
# `.group_default_token.irsyad`, `.group_default_token.cosem`, …
_GROUP_POINTER_PREFIX = ".group_default_token"

# Two-WORD families that must NOT collapse to their first segment (op#22426:
# cosem-tdu needs its OWN token pool, on Syed, distinct from the broader 'cosem'
# family which stays on Musa per op#16101's cosem-exams pin). A bare single-
# segment split would fold `cosem-tdu`/`cosem-tdu-coord` into family 'cosem' and
# silently boot TDU lanes on the wrong account.
#
# Considered and rejected: renaming the live `cosem-tdu` fleet_lanes row (and its
# future `cosem-tdu-coord`/autoscaler siblings) to a bare `tdu` family instead of
# special-casing the split. Checked against the live fleet_lanes table first
# (op#22426 investigation): every existing cosem sub-lane (`cosem-exams`,
# `cosem-adcda`, `cosem-port`, `cosem-video`, `cosem-tdu`) uses the
# `cosem-<suffix>` convention with zero precedent for a bare sub-family name --
# renaming the EXISTING LIVE lane's primary key to break that convention is a
# needless, higher-risk identifier change for no gain a prefix-match doesn't
# already give. This set is checked as a prefix match (exact, or `<compound>-`)
# BEFORE the generic split, so it also auto-covers a future `cosem-tdu-worker-N`
# autoscaler pool (mirrors the existing `irsyad-worker-N` convention) with no
# further edits here.
_COMPOUND_FAMILIES = {"cosem-tdu"}


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
    for compound in _COMPOUND_FAMILIES:
        if s == compound or s.startswith(compound + "-"):
            return compound
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


def _load_fp_map(map_path: Optional[str] = None) -> Dict[str, str]:
    """account -> fp map from token_fps.map (TOKEN_FPS_MAP env overrides). Never
    raises: a missing/unreadable map yields an empty dict (fail-open)."""
    p = map_path or os.environ.get("TOKEN_FPS_MAP") or _DEFAULT_FP_MAP
    out: Dict[str, str] = {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and not parts[0].startswith("#"):
                    out[parts[0]] = parts[1]
    except Exception:
        return {}
    return out


def account_for_fp(fp: Optional[str], map_path: Optional[str] = None) -> Optional[str]:
    """Reverse-lookup an OAuth fingerprint -> its account name via token_fps.map
    (map-driven so a rotation is handled with no code change). None for a falsy or
    unmapped fp. Never raises (fail-open). Mirrors token_file_guard.sh account_for_fp."""
    if not fp:
        return None
    for acct, mapped in _load_fp_map(map_path).items():
        if mapped == fp:
            return acct
    return None


def _parse_iso_utc(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp to an aware UTC datetime, or None if unparseable.
    A trailing 'Z' is normalised (py3.9 fromisoformat does not accept it); a naive
    value is assumed UTC. Never raises."""
    try:
        t = s.strip()
        if not t:
            return None
        if t[-1] in ("Z", "z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _musa_freeze_active(orch_dir: str) -> bool:
    """Is the Musa-personal freeze ACTIVE? True iff `$ORCH_DIR/.musa_personal_freeze`
    EXISTS and (its expiry line is empty/unparseable OR now_utc < expiry). An absent/
    unreadable flag => False (fail-OPEN: no freeze == byte-identical old behaviour).
    A present-but-EXPIRED flag => False. Never raises."""
    try:
        with open(os.path.join(orch_dir, _MUSA_FREEZE_FLAG), "r") as f:
            raw = f.read().strip()
    except Exception:
        return False                       # absent / unreadable => NOT active
    if not raw:
        return True                        # present, no expiry => active indefinitely
    expiry = _parse_iso_utc(raw.splitlines()[0])
    if expiry is None:
        return True                        # unparseable expiry => active (can't prove expired)
    return datetime.now(timezone.utc) < expiry   # expired => NOT active


def _apply_musa_freeze(resolved: Optional[str], session: str, orch_dir: str) -> Optional[str]:
    """Enforce the Musa-personal freeze on a WORKER-LANE resolution (op#28857).

    Freeze INACTIVE, or the resolved account is not Musa-personal => return `resolved`
    UNCHANGED (byte-identical back-compat). Freeze ACTIVE + the lane would boot on
    Musa-personal (an explicit Musa path, OR None which falls to the .env Musa
    default) => substitute `<keys_dir>/syed-oauth-token` after verifying it hashes to
    the `syed` account; if no valid Syed substitute exists, FAIL-CLOSED with the
    refuse sentinel — NEVER the Musa path and NEVER None.

    Applied to worker lanes only: the console (nazim/cc-orchestrator, reserved ON
    Musa by design) and the no-pointer singletons boot via their own launchers that
    do not consult this resolver's CLI, so redirecting them here would only create a
    display-vs-boot divergence — and the directive is about LANES."""
    if not _musa_freeze_active(orch_dir):
        return resolved

    if resolved is None:
        would_boot_musa = True             # None => caller falls to .env == Musa-personal
        keys_dir = None
    else:
        would_boot_musa = account_for_fp(_token_fp_at(resolved)) == _MUSA_ACCOUNT
        keys_dir = os.path.dirname(os.path.abspath(os.path.expanduser(resolved.strip())))

    if not would_boot_musa:
        return resolved                    # Syed (or any non-Musa) target -> unchanged

    # Would boot Musa-personal under an ACTIVE freeze -> substitute Syed, verified.
    if keys_dir is not None:
        cand = os.path.join(keys_dir, _SYED_TOKEN_FILENAME)
        if account_for_fp(_token_fp_at(cand)) == _SYED_ACCOUNT:
            sys.stderr.write(
                "[lane_token_resolver] MUSA-FREEZE active: session %r would boot on "
                "Musa-personal; substituting Syed (%s)\n" % (session, cand))
            return cand

    # FAIL-CLOSED: no valid Syed substitute (the None/.env edge has no keys dir, or
    # syed-oauth-token is missing/unreadable/mislabelled). Refuse — never Musa/None.
    sys.stderr.write(
        "[lane_token_resolver] MUSA-FREEZE active: session %r would boot on Musa-personal "
        "and NO valid Syed substitute exists -> REFUSING (fail-closed; NOT falling through "
        "to the .env Musa account)\n" % (session,))
    return _MUSA_FREEZE_REFUSE


def resolve_lane_token_path(session: str, orch_dir: Optional[str] = None) -> Optional[str]:
    """Resolve `session` -> ABSOLUTE PATH of the token key file it is CONFIGURED to
    boot on, honoring tiers 2 (per-session; 2b for a worker lane) → 3 (per-group) →
    4 (fleet default), or
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

    # Singletons with no group/fleet tier -- but DO check their own bespoke
    # pointer file first (see _SINGLETON_OWN_POINTER above); only an absent/
    # unreadable own-pointer falls through to None (.env), same fail-open shape
    # as tier 2.
    if session in _NO_POINTER_SINGLETONS:
        own = _SINGLETON_OWN_POINTER.get(session)
        return _read_pointer_target(orch_dir, own) if own else None

    # Worker lane — Tier 2b (per-SESSION), Tier 3 (per-GROUP), Tier 4 (fleet default).
    resolved: Optional[str] = None
    own_target = _read_pointer_target(orch_dir, "%s.%s" % (_LANE_SESSION_POINTER_PREFIX, session))
    if own_target:
        fp = _token_fp_at(own_target)
        if fp is not None and fp not in _FORBIDDEN_TOKEN_FPS:
            resolved = own_target

    fam = family_of(session)
    if resolved is None and fam:
        group_target = _read_pointer_target(orch_dir, "%s.%s" % (_GROUP_POINTER_PREFIX, fam))
        if group_target:
            fp = _token_fp_at(group_target)
            # Honor the group pin ONLY if its target is a readable, non-forbidden
            # token. An unreadable/empty target OR a forbidden fp -> fall through to
            # the fleet default (fail-open + refusal), never the .env floor.
            if fp is not None and fp not in _FORBIDDEN_TOKEN_FPS:
                resolved = group_target

    # Tier 4 — fleet default (used as-is; the caller's readability guard is the
    # tier-5 .env fallback, byte-identical to the pre-GAP-B fleet-default handling).
    if resolved is None:
        resolved = _read_pointer_target(orch_dir, _FLEET_DEFAULT_POINTER)

    # Musa-personal FREEZE guard (op#28857): worker lanes only, no-op when no flag.
    return _apply_musa_freeze(resolved, session, orch_dir)


def _main(argv=None) -> int:
    """CLI shim for launch_dangerous_cc.sh: print the resolved token key-file path
    (or NOTHING for a None resolution) and exit 0. Prints ONLY the path on stdout —
    no log noise — so the caller can `$(...)` it directly."""
    import argparse

    ap = argparse.ArgumentParser(description="Resolve a lane's configured token key file path (GAP-B).")
    ap.add_argument("--session", default="", help="tmux session / lane name (e.g. irsyad-coord)")
    ap.add_argument("--orch-dir", default=None, help="orchestrator dir (defaults to this repo root)")
    args = ap.parse_args(argv)
    try:
        path = resolve_lane_token_path(args.session, orch_dir=args.orch_dir)
    except Exception:
        # Fail-open at the boundary too: never let a resolver error break a launch.
        path = None
    # Musa-personal FREEZE refusal (op#28857): print NOTHING (so the launcher never
    # gets a path to boot Musa on) and exit with a DISTINCT non-zero code (42) that a
    # launcher can test to ABORT the boot rather than fall through to the .env Musa
    # account. `_apply_musa_freeze` already logged the loud reason to stderr.
    if path == _MUSA_FREEZE_REFUSE:
        sys.stderr.write(
            "[lane_token_resolver] REFUSE(42): Musa-personal freeze active and no Syed "
            "substitute for session %r.\n" % (args.session,))
        return 42
    if path:
        sys.stdout.write(path + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(_main())
