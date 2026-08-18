#!/usr/bin/env python3
"""register_fleet_health.py — seed the cc-fleet-health SRE identity + lane row.

The first dedicated-PERSISTENT agent under the 2026-07-21 target topology
(reports/fleet-target-topology-20260721.md). Idempotent registration migration,
applied via direct psycopg (NOT `supabase db push` — CLAUDE.md substrate rule).

Seeds two rows, both ON CONFLICT no-ops on re-run:
  1. agents(id='cc-fleet-health')  — FK target so the SRE can post bus rows.
  2. fleet_lanes(lane='fleet-health') — registry entry, Mini-hosted, operator-
     booted via scripts/boot_fleet_health.sh (mirrors the cai row: not
     lanes.sh-managed, desired_state='down').

The agents table has NO identity trigger (only agent_status does), so the INSERT
needs no app.current_agent_id GUC. Run once before the first boot:

    cd ~/wingmen/orchestrator && .venv/bin/python3 -m scripts.register_fleet_health
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ORCH_DIR, ".env"))

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not DSN:
    sys.exit("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env")

# fleet_lanes.launcher is an allow-list CHECK ('launch_dangerous_cc.sh',
# 'boot_cai.sh'). Additively widen it to admit this SRE's dedicated launcher —
# same class of change as the boot_cai.sh entry (a singleton with its own boot
# script). Idempotent: DROP IF EXISTS then re-add with the widened list.
LAUNCHER_CHECK_SQL = """
ALTER TABLE fleet_lanes DROP CONSTRAINT IF EXISTS fleet_lanes_launcher_check;
ALTER TABLE fleet_lanes ADD CONSTRAINT fleet_lanes_launcher_check
    CHECK (launcher = ANY (ARRAY[
        'launch_dangerous_cc.sh'::text,
        'boot_cai.sh'::text,
        'boot_fleet_health.sh'::text
    ]));
"""

AGENTS_SQL = """
INSERT INTO agents (id, display_name, repo_scope, status)
VALUES (
    'cc-fleet-health',
    'cc-fleet-health — Fleet SRE (detect->act self-healing, Mini)',
    ARRAY['*']::text[],
    'idle'
)
ON CONFLICT (id) DO NOTHING;
"""

FLEET_LANES_SQL = """
-- NOTE: fleet_lanes.branch is a REGISTRATION-TIME SNAPSHOT — informational only, NOT
-- authoritative and NOT maintained (it drifts freely as lanes switch branches). The real
-- branch is LIVE state (tmux pane_current_path / git HEAD in the worktree); recycle/discovery
-- resolves the worktree from live cwd, never from this column (Phase-3.5 wrong-worktree fix).
-- Never key tooling on it. See COMMENT ON COLUMN fleet_lanes.branch / bus #27924.
INSERT INTO fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES (
    'fleet-health',
    '/Users/sheikhmusa/wingmen/fleet-health',
    NULL,
    'claude-opus-4-8',
    'boot_fleet_health.sh',
    'cc-fleet-health',
    'down',
    'Persistent fleet SRE (host=Sheikhs-Mini). First dedicated-persistent agent under the 2026-07-21 target topology (reports/fleet-target-topology-20260721.md). Owns the fleet detect->act + self-healing loops so upkeep stops routing through Nazim/hub. Nudge/schedule-driven, NOT a hot loop. Operator-booted via boot_fleet_health.sh (singleton, no sub-tag; not lanes.sh-managed, hence desired_state=down). Boundary: does NOT assert the hub fleet-status pen or take the hub watchdog pen until cai clears the handoff (bus 10556); destructive context-auto-reset stays --arm-gated/dry-run.'
)
ON CONFLICT (lane) DO NOTHING;
"""


def main() -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(LAUNCHER_CHECK_SQL)
        cur.execute(AGENTS_SQL)
        agents_rows = cur.rowcount
        cur.execute(FLEET_LANES_SQL)
        lanes_rows = cur.rowcount
        conn.commit()

        # Verify + report final state (idempotent-safe: reports rows regardless
        # of whether this run inserted them).
        cur.execute(
            "SELECT id, display_name, repo_scope, status FROM agents WHERE id='cc-fleet-health'"
        )
        agent = cur.fetchone()
        cur.execute(
            "SELECT lane, launcher, base_agent_id, desired_state FROM fleet_lanes WHERE lane='fleet-health'"
        )
        lane = cur.fetchone()

    print(f"agents insert   : {'inserted' if agents_rows else 'already present'} -> {agent}")
    print(f"fleet_lanes ins : {'inserted' if lanes_rows else 'already present'} -> {lane}")
    if not agent or not lane:
        sys.exit("ERROR: registration verification failed — row missing after apply")
    print("OK: cc-fleet-health registered (agents + fleet_lanes).")


if __name__ == "__main__":
    main()
