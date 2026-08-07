#!/usr/bin/env python3
"""weekly_substrate_audit_gate.py — headroom gate for the weekly substrate audit (op#10385).

Decides whether there is enough use-it-or-lose-it weekly Max headroom to spend on a
Fable-5 ultracode substrate/repo audit BEFORE the weekly reset, and on WHICH pool.

Doctrine: weekly token pools are use-it-or-lose-it ([[feedback_weekly_token_pools_use_it]]);
spending soon-to-expire headroom on a self-audit ([[project_fleet_self_audit]]) is upside.
The audit must run on the pool that is about to reset AND has headroom — NEVER a near-spent
pool (which would starve real work). Reads live utilization via the proven weekly_limit_monitor
probe (anthropic-ratelimit-unified-7d-utilization). Read-only; makes no decisions to spend.

Exit: 0 = GO (prints chosen pool), 2 = SKIP (insufficient headroom), 1 = error.
Usage: weekly_substrate_audit_gate.py [--threshold 0.30] [--json]
  --threshold F  minimum headroom fraction (1 - utilization) to GO (default 0.30)
"""
import json
import sys
import time
from pathlib import Path

# Bootstrap sys.path so this runs under launchd's bare environment too (op#9393 PATH lesson).
_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

from nervous_system import weekly_limit_monitor as wlm

DEFAULT_THRESHOLD = 0.30  # >=30% headroom (<=70% utilization) to justify the audit spend
DEFAULT_IMMINENT_HOURS = 6.0  # only spend headroom that is ABOUT TO RESET within this window


def evaluate(threshold: float = DEFAULT_THRESHOLD,
             imminent_hours: float = DEFAULT_IMMINENT_HOURS) -> dict:
    """GO iff a pool whose reset is IMMINENT (use-it-or-lose-it) still has >=threshold headroom.

    A pool that just reset (fresh, high headroom, but reset is 7d away) is NOT a candidate —
    spending it would eat the NEW week's budget, not expiring surplus. So we filter to pools
    resetting within `imminent_hours`, then pick the most-headroom among those.
    """
    now = time.time()
    pools = []
    for name in wlm.POOLS:
        try:
            p = wlm.probe_pool(name)
        except Exception as e:  # a pool we cannot read is not a GO candidate
            pools.append({"pool": name, "error": repr(e)[:160], "headroom": None})
            continue
        u7 = p.get("u7d")
        if u7 is None:
            pools.append({"pool": name, "error": "no u7d header (idle/no-data)", "headroom": None})
            continue
        reset_epoch = p.get("reset7d")
        try:
            hrs = (float(reset_epoch) - now) / 3600.0 if reset_epoch is not None else None
        except (TypeError, ValueError):
            hrs = None
        pools.append({
            "pool": name,
            "u7d": round(u7, 4),
            "headroom": round(1.0 - u7, 4),
            "reset7d": wlm._fmt_reset(reset_epoch),
            "hours_to_reset": round(hrs, 1) if hrs is not None else None,
            "imminent": bool(hrs is not None and 0 <= hrs <= imminent_hours),
            "status7d": p.get("status7d"),
        })

    # Only pools whose reset is imminent are use-it-or-lose-it candidates.
    candidates = [p for p in pools if p.get("headroom") is not None and p.get("imminent")]
    best = max(candidates, key=lambda p: p["headroom"], default=None)
    go = bool(best and best["headroom"] >= threshold)
    any_readable = [p for p in pools if p.get("headroom") is not None]
    if go:
        reason = (f"GO on {best['pool']} ({best['headroom']*100:.0f}% headroom, "
                  f"resets in {best['hours_to_reset']}h @ {best['reset7d']})")
    elif best:
        reason = (f"SKIP — imminent pool {best['pool']} headroom {best['headroom']*100:.0f}% "
                  f"< {threshold*100:.0f}% threshold")
    elif any_readable:
        reason = (f"SKIP — no pool resets within {imminent_hours:.0f}h "
                  f"(nothing expiring to spend)")
    else:
        reason = "SKIP — no readable pool"
    return {
        "go": go,
        "threshold": threshold,
        "imminent_hours": imminent_hours,
        "chosen_pool": best["pool"] if go else None,
        "chosen_headroom": best["headroom"] if go else (best["headroom"] if best else None),
        "reason": reason,
        "pools": pools,
    }


def main() -> int:
    threshold = DEFAULT_THRESHOLD
    as_json = "--json" in sys.argv
    if "--threshold" in sys.argv:
        try:
            threshold = float(sys.argv[sys.argv.index("--threshold") + 1])
        except (IndexError, ValueError):
            print("bad --threshold value", file=sys.stderr)
            return 1
    d = evaluate(threshold)
    if as_json:
        print(json.dumps(d, indent=2))
    else:
        print(d["reason"])
        for p in d["pools"]:
            if p.get("headroom") is not None:
                print(f"  {p['pool']:6} util={p['u7d']*100:.0f}% headroom={p['headroom']*100:.0f}% "
                      f"reset_in={p.get('hours_to_reset')}h imminent={p.get('imminent')} "
                      f"status={p.get('status7d')}")
            else:
                print(f"  {p['pool']:6} unreadable: {p.get('error')}")
    return 0 if d["go"] else 2


if __name__ == "__main__":
    sys.exit(main())
