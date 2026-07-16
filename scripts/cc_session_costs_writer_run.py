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

from nervous_system.cc_session_costs_auto_writer import sweep_projects_root, upsert_rows

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
    rows = sweep_projects_root(root, modified_since=cutoff, body_role=body_role)
    print(f"[cc-writer] {len(rows)} session rows from {root} (last {args.lookback_seconds}s)")
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
