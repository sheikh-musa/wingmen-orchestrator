"""handoff_find — the ONE definition of "this lane's newest handoff file" (CAI-1392 C).

A lane's handoff lives in the orchestrator reports/ dir under one of two names that both
occur in the wild: `<short>-handoff-*.md` (storefront-handoff-NOW.md) and
`cc-<short>-handoff-*.md` in any case (cc-storefront-HANDOFF-20261002.md). The old worker
glob saw only the first, case-sensitively, so on 2026-10-02 it could not see the fresh
handoff cc-storefront had just written and fell back to a 12-day-old one (fail-closed, but
it blocks every correct recycle). Shared by sre_lane_recycle.py and reset_auditor.sh so the
two cannot drift.

Freshness is the TARGET's mtime (os.stat follows symlinks): self_recycle.sh once read a
symlink's own mtime and called a seconds-old handoff 25 days stale.

CLI:  handoff_find.py --reports <dir> --base <base_agent_id>
      prints "<abs path>\t<int mtime>" and exits 0, or prints nothing and exits 1.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _short(base_agent_id: str) -> str:
    return base_agent_id[3:] if base_agent_id.startswith("cc-") else base_agent_id


def newest_handoff_with_mtime(reports_dir, base_agent_id: str):
    """(absolute resolved path, target mtime) of the newest handoff, or (None, None)."""
    pat = re.compile(r"^(cc-)?" + re.escape(_short(base_agent_id)) + r"-handoff-.*\.md$",
                     re.IGNORECASE)
    best, best_m = None, None
    try:
        names = os.listdir(reports_dir)
    except OSError:
        return None, None
    for name in names:
        if not pat.match(name):
            continue
        p = Path(reports_dir) / name
        try:
            m = os.stat(p).st_mtime          # follows symlinks; broken link -> OSError
        except OSError:
            continue
        if best_m is None or m > best_m:
            best, best_m = p, m
    if best is None:
        return None, None
    return str(best.resolve()), best_m


def newest_handoff(reports_dir, base_agent_id: str):
    return newest_handoff_with_mtime(reports_dir, base_agent_id)[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reports", required=True)
    ap.add_argument("--base", required=True)
    a = ap.parse_args(argv)
    path, mtime = newest_handoff_with_mtime(a.reports, a.base)
    if path is None:
        return 1
    print(f"{path}\t{int(mtime)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
