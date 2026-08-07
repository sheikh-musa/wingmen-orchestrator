#!/usr/bin/env python3
"""Periodic cc_session_costs writer — one incremental sweep + upsert per run.

Replaces the in-orchestrator sweep that lived in wingmen_orch.py's main loop.
That daemon stopped running on 2026-07-08 when the hub topology migrated from
the always-on Python orchestrator (Mac Mini) to the interactive Claude Code
`orch` tmux session (Studio, launchd dev.wingmen.cc-orch). Nothing has written
cc_session_costs since. This launchd-driven runner restores the writes.

Runs on whichever host it's scheduled on and sweeps that host's
~/.claude/projects. cc_identity resolution is host-agnostic (see
cc_session_costs_auto_writer._canonical_dir), so it works on the Studio
(home /Users/Musa) as well as the Mini (home /Users/sheikhmusa).

Each run does an incremental sweep of jsonls modified in the last LOOKBACK
seconds and upserts (keyed on session_id + source) — so an active session's
row, including its `latest_context_tokens` (live window fill), is refreshed
every run. Idempotent; safe to run every few minutes via launchd StartInterval.

Usage:
    cc_session_costs_writer_run.py [--lookback-seconds N] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_ORCH_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_ORCH_DIR / ".env")
sys.path.insert(0, str(_ORCH_DIR))

from nervous_system.cc_session_costs_auto_writer import (
    sweep_projects_root, upsert_rows, lane_dir_map_from_fleet_lanes,
    lane_subtag_map_from_fleet_lanes)

_SOURCE = "auto_writer_v1"


def main() -> int:
    p = argparse.ArgumentParser()
    # Default lookback comfortably exceeds the launchd interval so a session
    # that was quiet for one cycle is still recaptured on the next.
    p.add_argument("--lookback-seconds", type=int, default=7200)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    root = Path.home() / ".claude" / "projects"
    cutoff = time.time() - args.lookback_seconds
    # Body-aware: on the Mini the console body (Nazim) shares the orchestrator
    # repo dir with the hub — pass ORCH_BODY_ROLE so its sessions land as
    # `orch-console`, not commingled into the hub's `cc-orchestrator` gauge.
    body_role = os.environ.get("ORCH_BODY_ROLE")
    # DYNAMIC worker-lane coverage (op#15406 Gap-1): resolve every REGISTERED
    # worker lane's ~/.claude/projects dir from fleet_lanes.worktree_path, so a
    # newly-registered lane is measured without a hand-edit to the static map.
    # Fail-safe: {} on any DB error (writer still runs with the static map).
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    extra_map = lane_dir_map_from_fleet_lanes(dsn) if dsn else {}
    # Per-instance sub_tag for multi-lane families (op#10550) so a perimeter's
    # instances record distinct context readings, not one shared cc_identity row.
    subtag_map = lane_subtag_map_from_fleet_lanes(dsn) if dsn else {}
    rows = sweep_projects_root(root, modified_since=cutoff, body_role=body_role,
                               extra_dir_map=extra_map, subtag_map=subtag_map)
    print(f"[cc-writer] {len(rows)} session rows from {root} "
          f"(last {args.lookback_seconds}s; +{len(extra_map)//2} dynamic lane dirs, "
          f"{len(subtag_map)//2} family sub_tags)")
    if not rows:
        return 0
    if args.dry_run:
        for r in rows:
            print(f"  {r['cc_identity']:20} sess={r['session_id'][:8]} "
                  f"ctx={r['latest_context_tokens']:,}")
        return 0

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("[cc-writer] ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    written = upsert_rows(dsn, rows, source=_SOURCE)
    print(f"[cc-writer] upserted {written} rows (source={_SOURCE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
