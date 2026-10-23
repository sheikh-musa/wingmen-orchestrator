#!/usr/bin/env python3
"""provider_policy_backfill_check.py — re-run the lane -> client_key mapping
against EVERY fleet_lanes row (orch-console's guardrail, bus #74588: "must pass
clean before flipping warn-only to hard-refuse").

Reports, for every registered lane: the resolved client_key (or None — unmapped,
fails closed to Anthropic-only), the effective policy (after any lane override),
and whether the lane is CURRENTLY on a glm-* model pointer while the new gate
would refuse it (the actual thing this check exists to catch before the gate goes
live and would hard-refuse something that's working today).

DETECT-ONLY. Never writes anything, never flips a lane, never pages — this is a
pre-flight report a human/cc-fleet-health reads before the hard-refuse rollout
step, same spirit as every other --dry-run-first tool in this fleet.

Usage (from ~/wingmen/orchestrator):
    .venv/bin/python3 scripts/provider_policy_backfill_check.py           # human-readable
    .venv/bin/python3 scripts/provider_policy_backfill_check.py --json    # machine-readable
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

ORCH_DIR = Path(os.environ.get("ORCH_DIR", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(ORCH_DIR))

from scripts.lib import provider_policy as pp  # noqa: E402


def classify_lane(lane: str, policy_rows: List[dict], override_rows: List[dict],
                   currently_glm: bool) -> dict:
    """PURE. One lane's backfill result: resolved client_key, effective policy,
    whether it's currently on a glm-* pointer, and whether the new gate would
    REFUSE it (the actual signal this tool exists to surface)."""
    client_keys = [r.get("client_key") for r in policy_rows]
    key = pp.resolve_client_key(lane, client_keys)
    client_level = pp.resolve_policy(rows=policy_rows, client_key=key) if key is not None else dict(pp.ANTHROPIC_ONLY)
    override = pp.lookup_lane_override(override_rows, lane)
    misconfigured = False
    if override is not None:
        if pp.is_narrower_or_equal(override, client_level):
            effective = override
        else:
            misconfigured = True
            effective = override  # report what WOULD be used; flagged as misconfigured
    else:
        effective = client_level
    zai_allowed = "zai" in effective["allowed_providers"]
    would_refuse_if_glm = currently_glm and not zai_allowed
    return {
        "lane": lane,
        "resolved_client_key": key,
        "effective_allowed_providers": effective["allowed_providers"],
        "override_applied": override is not None,
        "override_misconfigured": misconfigured,
        "currently_glm": currently_glm,
        "would_refuse_if_glm": would_refuse_if_glm,
    }


def glm_pointer_lanes(orch_dir: Path) -> set:
    """Every session whose .{session}_model file currently says glm-* — same
    signal glm_overflow_switch.py/glm_peak_pause_resume.py already use."""
    out = set()
    for f in sorted(glob.glob(str(orch_dir / ".*_model"))):
        name = os.path.basename(f)
        if ".bak" in name:
            continue
        try:
            model = Path(f).read_text().strip()
        except OSError:
            continue
        if model.startswith("glm-"):
            out.add(name[1:-len("_model")])
    return out


def fetch_rows():
    import psycopg
    from scripts.bus_send import dburl
    dsn = dburl(os.environ)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT client_key, allowed_providers, text_only FROM provider_policy")
        cols = [d[0] for d in cur.description]
        policy_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT lane, allowed_providers, text_only FROM provider_policy_lane_override")
        cols = [d[0] for d in cur.description]
        override_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT lane FROM fleet_lanes ORDER BY lane")
        lanes = [r[0] for r in cur.fetchall()]
    return policy_rows, override_rows, lanes


def run(policy_rows, override_rows, lanes, glm_lanes) -> List[dict]:
    """PURE given its inputs — the DB/filesystem reads are factored out above so
    this is directly unit-testable."""
    return [classify_lane(l, policy_rows, override_rows, l in glm_lanes) for l in lanes]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    policy_rows, override_rows, lanes = fetch_rows()
    glm_lanes = glm_pointer_lanes(ORCH_DIR)
    results = run(policy_rows, override_rows, lanes, glm_lanes)

    problems = [r for r in results if r["would_refuse_if_glm"] or r["override_misconfigured"]]

    if a.json:
        print(json.dumps({"results": results, "problems": problems}, indent=1))
        return 1 if problems else 0

    unmapped = [r for r in results if r["resolved_client_key"] is None]
    print(f"{len(lanes)} lane(s) checked; {len(unmapped)} unmapped (fail-closed anthropic-only, "
          f"not necessarily a problem); {len(glm_lanes)} currently on a glm-* pointer.")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S) — must be clean before flipping warn-only to hard-refuse:")
        for r in problems:
            if r["override_misconfigured"]:
                print(f"  MISCONFIGURED OVERRIDE: {r['lane']} (client_key={r['resolved_client_key']!r}) "
                      f"— lane override is WIDER than its client-level policy")
            if r["would_refuse_if_glm"]:
                print(f"  WOULD REFUSE: {r['lane']} is currently on GLM but resolves to "
                      f"client_key={r['resolved_client_key']!r}, allowed={r['effective_allowed_providers']} "
                      f"(zai not allowed)")
    else:
        print("\nCLEAN — no lane currently on GLM would be refused by the new gate.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
