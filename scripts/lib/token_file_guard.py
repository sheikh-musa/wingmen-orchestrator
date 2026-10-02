"""token_file_guard.py: the python twin of token_file_guard.sh (orch-console #49107).

A key file NAMED <acct>-oauth-token must hash to <acct>'s fp in scripts/lib/token_fps.map, the
ONE place to update after a rotation. Both helpers read that same file, so they cannot drift.
Never returns or logs the token. Fail-closed: a missing map, or an unreadable or empty token,
refuses."""
from __future__ import annotations

import hashlib
import os
from typing import Dict, Optional, Tuple

DEFAULT_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_fps.map")


def load_map(map_path: Optional[str] = None) -> Dict[str, str]:
    p = map_path or os.environ.get("TOKEN_FPS_MAP") or DEFAULT_MAP
    out: Dict[str, str] = {}
    with open(p, "r", encoding="utf-8") as fh:            # OSError propagates -> caller refuses
        for line in fh:
            parts = line.split()
            if len(parts) >= 2 and not parts[0].startswith("#"):
                out[parts[0]] = parts[1]
    return out


def _acct(name: str) -> Optional[str]:
    return name[: -len("-oauth-token")] if name.endswith("-oauth-token") else None


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()[:12]


def check(path: str, map_path: Optional[str] = None) -> Tuple[bool, str]:
    """(True, "") if usable, else (False, reason). Judges the name used AND a symlink's target."""
    try:
        fps = load_map(map_path)
    except OSError:
        return False, "token_file_guard: REFUSING %r: fp map missing/unreadable (fail-closed)." % path
    names = [os.path.basename(path)]
    if os.path.islink(path):
        names.append(os.path.basename(os.path.realpath(path)))
    for n in names:
        acct = _acct(n)
        if not acct or acct not in fps:
            continue
        try:
            tok = open(path, "r", encoding="utf-8").read().strip()
        except OSError:
            return False, "token_file_guard: REFUSING %r: not readable." % path
        if not tok:
            return False, "token_file_guard: REFUSING %r: empty token file." % path
        got, expected = fingerprint(tok), fps[acct]
        if got != expected:
            owner = next((a for a, f in fps.items() if f == got), None)
            return False, ("token_file_guard: REFUSING %r: named %r (expects fp %s) but it hashes to %s (%s). "
                           "Either: fp map out of date (after a rotation, update scripts/lib/token_fps.map, "
                           "the ONE place) OR the file is mislabelled. Not using it."
                           % (path, acct, expected, got, ("= " + owner) if owner else "not in the map"))
    return True, ""
