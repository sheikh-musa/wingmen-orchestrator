"""Max-pool nickname ⇐ OAuth-token fingerprint — the ONE backend map (op#20684).

`auth_fp` = sha256(OAuth token)[:12] — a non-reversible short id the operator
already uses on the bus. The POOL NICKNAME ("Musa" / "musa2" / "Syed") is the
operational, safe-to-show label; the raw fp stays off the hosted (phone) view.

CANONICAL SET — keep these three in lockstep (a prefix mismatch = a lane with no
badge on one surface but a badge on the other):
  * this module (backend SSOT: panes.py `_KNOWN_ACCOUNTS`, hosted_view, app.py)
  * static/fleet.js  `POOL_FP` (dashboard chip + key roll-up, local + hosted)
  * static/irsyad.js `acctForFp` (standalone /irsyad view)
"""
from __future__ import annotations

from typing import Dict, Optional

# full 12-char fingerprint -> pool nickname (op#10706 Musa/Syed; op#12617 musa2).
KNOWN_POOLS: Dict[str, str] = {
    "68142948c003": "Musa",
    "e1dfa48eec85": "musa2",
    "582043088eae": "Syed",
}
# fleet.js matches on a SHORT prefix (7-9 chars); mirror that so an fp truncated
# by any writer still resolves to the same nickname on every surface.
_PREFIX = 7


def pool_for_fp(fp: Optional[str]) -> str:
    """Pool nickname for an auth fingerprint, or "" when unknown/absent.
    Never returns the fp itself — an unknown key is "" (no badge), not a leak."""
    fp = (fp or "").strip()
    if len(fp) < _PREFIX:
        return ""
    for full, name in KNOWN_POOLS.items():
        if full.startswith(fp[:len(full)]):   # fp is the full id or a >=7-char prefix of it
            return name
    return ""
