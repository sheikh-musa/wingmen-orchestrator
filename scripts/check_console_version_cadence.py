#!/usr/bin/env python3
"""check_console_version_cadence.py — warn on same-day console version churn.

op#10291 / the Aug-3 incident: shipping many console SW-version bumps in one day
(fc-v22 -> v27) repeatedly hard-reset the operator's PWA over the marginal
Abu-Dhabi<->Singapore relay and stranded it (cleared-cache + a dropped reload =
blank). Fix #1 = a going-forward DISCIPLINE: batch a day's console changes into
ONE version bump. This check makes that discipline visible AT the point of action.

It reads the current VERSION from sw.js, records the date each version was first
seen (logs/console_version_bumps.json), and WARNS (non-zero exit) if 2+ distinct
versions were first seen TODAY — i.e. you're about to re-churn same-day.

Run it before/after a console deploy:
    python3 scripts/check_console_version_cadence.py         # report + record + warn
Not a hard gate (it can't block a manual edit) — a loud reminder, on the record.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent
_SW = _ORCH / "nervous_system" / "console" / "static" / "sw.js"
_STAMP = _ORCH / "logs" / "console_version_bumps.json"


def _current_version() -> "str | None":
    try:
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', _SW.read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except Exception:
        return None


def main() -> int:
    ver = _current_version()
    if not ver:
        print("could not read VERSION from sw.js", file=sys.stderr)
        return 2
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        stamp = json.loads(_STAMP.read_text())
    except Exception:
        stamp = {}
    # Record this version's first-seen date (never overwrite an earlier date).
    if ver not in stamp:
        stamp[ver] = today
        try:
            _STAMP.parent.mkdir(parents=True, exist_ok=True)
            _STAMP.write_text(json.dumps(stamp, indent=2, sort_keys=True))
        except Exception as e:
            print(f"(warn: could not write stamp: {e})", file=sys.stderr)
    today_versions = sorted(v for v, d in stamp.items() if d == today)
    print(f"console VERSION={ver}; versions first-seen today ({today}): {today_versions or 'none'}")
    if len(today_versions) >= 2:
        print(f"⚠️  {len(today_versions)} console version bumps TODAY "
              f"({', '.join(today_versions)}) — this is the Aug-3 churn class that "
              f"strands the operator's PWA over the AD<->SG relay. BATCH further "
              f"changes into the SAME version until tomorrow.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
