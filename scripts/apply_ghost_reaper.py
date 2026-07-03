#!/usr/bin/env python3
"""Apply migrations/009_ghost_reaper.sql to the orch substrate (CAI-RESP-292).

Direct psycopg apply — NEVER `supabase db push` (per CLAUDE.md / decision 962:
the CLI shadow-diff path re-applies historic CREATE OR REPLACE VIEW bodies and
silently strips later boot_briefing arms). This script applies the exact file.
"""
import os
from pathlib import Path
import psycopg
from dotenv import load_dotenv

ORCH = Path(__file__).resolve().parent.parent
load_dotenv(str(ORCH / ".env"))
dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
sql = (ORCH / "migrations" / "009_ghost_reaper.sql").read_text()

with psycopg.connect(dsn) as c, c.cursor() as cur:
    cur.execute(sql)
    c.commit()
    # verify the artifacts exist
    cur.execute("select 1 from information_schema.columns where table_name='agent_status' and column_name='reaped_by'")
    assert cur.fetchone(), "reaped_by not added to agent_status"
    cur.execute("select 1 from pg_proc where proname='sweep_stale_agents'")
    assert cur.fetchone(), "sweep_stale_agents not created"
    print("applied 009_ghost_reaper.sql — reaped_by + sweep_stale_agents present")
