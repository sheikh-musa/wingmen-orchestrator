#!/usr/bin/env python3
"""register_quality.py — seed the cc-quality (Head of Quality) identity + lane row.

The fleet's engineering-quality + substrate-coherence node, governance-blessed by
cai (CAI-RESP-776/777/778). Distinct from cc-fleet-health (SRE/ops-health) — see
reports/cc-quality-charter.md. Idempotent registration migration, applied via
direct psycopg (NOT `supabase db push` — CLAUDE.md substrate rule).

Seeds two rows, both ON CONFLICT no-ops on re-run:
  1. agents(id='cc-quality')  — FK target so cc-quality can post bus rows.
  2. fleet_lanes(lane='quality') — registry entry, Mini-hosted, on-demand booted
     via scripts/boot_quality.sh (mirrors cai / fleet-health: not lanes.sh-managed,
     desired_state='down' — cc-quality is on-demand/scheduled per charter cond #1,
     NOT a 24/7 heartbeat lane).

The agents table has NO identity trigger (only agent_status does), so the INSERT
needs no app.current_agent_id GUC. Run once before the first boot:

    cd ~/wingmen/orchestrator && .venv/bin/python3 -m scripts.register_quality
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

# fleet_lanes.launcher is an allow-list CHECK. Additively widen it to admit
# cc-quality's dedicated launcher — same class of change as the boot_cai.sh /
# boot_fleet_health.sh entries (a singleton with its own boot script). Idempotent:
# DROP IF EXISTS then re-add with the widened list (keep all prior entries).
LAUNCHER_CHECK_SQL = """
ALTER TABLE fleet_lanes DROP CONSTRAINT IF EXISTS fleet_lanes_launcher_check;
ALTER TABLE fleet_lanes ADD CONSTRAINT fleet_lanes_launcher_check
    CHECK (launcher = ANY (ARRAY[
        'launch_dangerous_cc.sh'::text,
        'boot_cai.sh'::text,
        'boot_fleet_health.sh'::text,
        'boot_quality.sh'::text
    ]));
"""

AGENTS_SQL = """
INSERT INTO agents (id, display_name, repo_scope, status)
VALUES (
    'cc-quality',
    'cc-quality — Head of Quality (eng quality + substrate coherence, Mini)',
    ARRAY['*']::text[],
    'idle'
)
ON CONFLICT (id) DO NOTHING;
"""

FLEET_LANES_SQL = """
INSERT INTO fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES (
    'quality',
    '/Users/sheikhmusa/wingmen/quality',
    NULL,
    'claude-opus-4-8',
    'boot_quality.sh',
    'cc-quality',
    'down',
    'Head of Quality (host=Sheikhs-Mini). Owns engineering quality + substrate coherence: correctness, tests, cross-lane consistency, drift sweeps, deploy-provenance, docs, tech-debt. Governance-blessed by cai CAI-RESP-776/777/778. ON-DEMAND / scheduled (charter cond #1) — NOT a 24/7 heartbeat lane; runs for per-PR reviews + scheduled sweeps, idle otherwise (desired_state=down). Advisory: escalates governance/money/schema forks to cai. Single ledger = cc-fleet-health''s (CAI-771 + CAI-420), no duplicate. Booted via boot_quality.sh; charter = reports/cc-quality-charter.md.'
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

        cur.execute(
            "SELECT id, display_name, repo_scope, status FROM agents WHERE id='cc-quality'"
        )
        agent = cur.fetchone()
        cur.execute(
            "SELECT lane, launcher, base_agent_id, desired_state FROM fleet_lanes WHERE lane='quality'"
        )
        lane = cur.fetchone()

    print(f"agents insert   : {'inserted' if agents_rows else 'already present'} -> {agent}")
    print(f"fleet_lanes ins : {'inserted' if lanes_rows else 'already present'} -> {lane}")
    if not agent or not lane:
        sys.exit("ERROR: registration verification failed — row missing after apply")
    print("OK: cc-quality registered (agents + fleet_lanes).")


if __name__ == "__main__":
    main()
