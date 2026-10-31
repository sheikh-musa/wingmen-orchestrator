#!/usr/bin/env python3
"""register_quality_2.py — register cc-quality-2, the fleet's SECOND reviewer.

Directed by orch-console (bus #87390, 2026-10-31): cc-quality had ~12 PRs queued and
became the bottleneck of the build-review loop once the builders moved to DeepSeek.
cc-quality-2 takes the cosem-adcda + cosem-platform PRs so the two reviewers run in
parallel. Same charter and standards as cc-quality; on an Anthropic model on the Syed
token so the review stays independent of the DeepSeek builders it reviews.

Mirrors scripts/register_quality.py exactly, including the additive widening of the
fleet_lanes.launcher allow-list (the sanctioned pattern used by register_quality.py
and register_fleet_health.py when a lane gets its own boot script).

Idempotent. Applied via direct psycopg (NOT `supabase db push` — CLAUDE.md rule).

    ~/wingmen/orchestrator/.venv/bin/python3 ~/wingmen/quality-2/register_quality_2.py

TWO SCHEMA FACTS THIS RESPECTS (both verified live 2026-10-31, neither is a guess):
  * agent_status has CHECK (base_agent_id = regexp_replace(agent_id,'-[0-9]+$','')).
    So an agent_id of 'cc-quality-2' MUST carry base_agent_id='cc-quality'. The DB
    itself models cc-quality-2 as an INSTANCE of the cc-quality base family.
  * fleet_lanes.base_agent_id -> agents(id) FK, and fleet_lanes.launcher is an
    allow-list CHECK. We widen the latter additively (never drop prior entries) and
    key this lane's fleet_lanes row on 'cc-quality-2' so lane routing resolves EXACTLY
    to the quality-2 session. Keying it on 'cc-quality' would collide with the
    'quality' lane in lane_map (a base->one-lane dict) and could silently route a
    cc-quality-2 nudge into cc-quality's pane.
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

ORCH_DIR = os.path.expanduser("~/wingmen/orchestrator")
load_dotenv(os.path.join(ORCH_DIR, ".env"))

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
if not DSN:
    sys.exit("ERROR: DATABASE_URL / SUPABASE_DB_URL not set in .env")

# Additively widen the launcher allow-list (KEEP every prior entry).
LAUNCHER_CHECK_SQL = """
ALTER TABLE fleet_lanes DROP CONSTRAINT IF EXISTS fleet_lanes_launcher_check;
ALTER TABLE fleet_lanes ADD CONSTRAINT fleet_lanes_launcher_check
    CHECK (launcher = ANY (ARRAY[
        'launch_dangerous_cc.sh'::text,
        'boot_cai.sh'::text,
        'boot_fleet_health.sh'::text,
        'boot_quality.sh'::text,
        'boot_quality_2.sh'::text
    ]));
"""

# The agents row already exists (auto-registered 2026-10-07); make its display_name
# tell the truth instead of "auto-registered instance (launcher)".
AGENTS_SQL = """
UPDATE agents
   SET display_name = 'cc-quality-2 — second reviewer (cosem-adcda + cosem-platform, Syed, opus-4-8)',
       status = 'active'
 WHERE id = 'cc-quality-2';
"""

FLEET_LANES_SQL = """
INSERT INTO fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES (
    'quality-2',
    '/Users/sheikhmusa/wingmen/quality-2',
    NULL,
    'claude-opus-4-8',
    'boot_quality_2.sh',
    'cc-quality-2',
    'on-demand',
    'SECOND reviewer (orch-console #87390, 2026-10-31). Same charter + standards as cc-quality; splits the review queue so the reviewer stops being the build-review bottleneck now that builders run on DeepSeek. TAKES: cosem-adcda + cosem-platform PRs. cc-quality KEEPS: orchestrator + everything else + any secrets-P0. On an ANTHROPIC model (claude-opus-4-8) on purpose — review must stay independent of the DeepSeek builders it reviews. Token = Syed (~/.wingmen/keys/syed-oauth-token, fp 582043088eae), NEVER Musa. ON-DEMAND (charter cond #1): no perpetual lease, no 24/7 busywork; idle when the queue is clear. Booted via ~/wingmen/quality-2/boot_quality_2.sh under a RunAtLoad+KeepAlive plist (dev.wingmen.quality-2-session). Inherits all five cai conditions (CAI-RESP-776/777/778). Single ledger = cc-fleet-health''s.'
)
ON CONFLICT (lane) DO UPDATE
  SET worktree_path=EXCLUDED.worktree_path, model=EXCLUDED.model,
      launcher=EXCLUDED.launcher, base_agent_id=EXCLUDED.base_agent_id,
      desired_state=EXCLUDED.desired_state, notes=EXCLUDED.notes, updated_at=now();
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
            "SELECT id, display_name, status FROM agents WHERE id='cc-quality-2'"
        )
        agent = cur.fetchone()
        cur.execute(
            "SELECT lane, launcher, base_agent_id, desired_state, model "
            "FROM fleet_lanes WHERE lane='quality-2'"
        )
        lane = cur.fetchone()

    print(f"agents update   : {agents_rows} row(s) -> {agent}")
    print(f"fleet_lanes     : {lanes_rows} row(s) -> {lane}")
    if not agent or not lane:
        sys.exit("ERROR: registration verification failed — row missing after apply")
    print("OK: cc-quality-2 registered (agents + fleet_lanes, launcher allow-list widened).")


if __name__ == "__main__":
    main()
