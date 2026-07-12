-- 024_coordinator_panes.sql — cross-host coordinator pane snapshots (op #3729).
--
-- The fleet console peeks a lane's LIVE tmux pane locally. Coordinator bodies
-- that run OFF the console host (Nazim's 'nazim' tmux lives on the Mini) can't be
-- peeked that way — and the console must NOT gain outbound SSH to reach them
-- (attack-surface expansion; reverted per operator #3729 / Nazim #7877). Instead
-- a Mini-SIDE publisher captures the pane and UPSERTs it here; the console (which
-- already reads the substrate) READS this row. Zero SSH, console stays
-- read-only/no-outbound-network. Closes the heartbeat-vs-reality gap (task #13)
-- for coordinators.
--
-- One row per coordinator (PK agent_id) = latest snapshot; captured_at lets the
-- console reject a stale snapshot and fall back to the bus-activity feed.
--
-- Substrate table (coordination fabric), service-role-only — same posture as
-- migrations 013/014/020. Additive + IF NOT EXISTS. Apply via direct psycopg
-- (scripts/apply_coordinator_panes.py), NEVER `supabase db push` (CLAUDE.md /
-- decision 962). Safe to apply ahead of both the publisher and the console
-- read-switch: nothing references it yet, so creating it is a no-op for every
-- running process.

CREATE TABLE IF NOT EXISTS coordinator_panes (
    agent_id     text        PRIMARY KEY,   -- coordinator body's from_agent (e.g. 'orch-console')
    pane_text    text        NOT NULL,      -- latest captured tmux pane (publisher writes it)
    captured_at  timestamptz NOT NULL DEFAULT now()
);

-- Lockdown — mirror agent_status EXACTLY (the table the console already reads):
-- RLS on, no BYPASSRLS anywhere, and role-specific ALLOW policies. console_readonly
-- (the SELECT-only role the console pool uses) needs BOTH a table GRANT and an RLS
-- SELECT policy — a table GRANT alone yields empty reads (RLS filters every row),
-- which is exactly what a deny-all-to-public policy did in the first cut of this
-- migration. service_role (publisher) keeps full DML. All DROP/CREATE so the whole
-- migration is re-runnable (safe recreate pairs).
ALTER TABLE coordinator_panes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON coordinator_panes FROM PUBLIC, anon, authenticated;
GRANT SELECT ON coordinator_panes TO console_readonly;

DROP POLICY IF EXISTS deny_all_coordinator_panes ON coordinator_panes;  -- from the first cut; superseded
-- rls-policy-exempt: coordinator_panes console read (SELECT-only role, mirrors agent_status_console_ro)
DROP POLICY IF EXISTS coordinator_panes_console_ro ON coordinator_panes;
CREATE POLICY coordinator_panes_console_ro ON coordinator_panes FOR SELECT TO console_readonly USING (true);
-- rls-policy-exempt: coordinator_panes service writes (publisher; mirrors agent_status_service_only)
DROP POLICY IF EXISTS coordinator_panes_service_only ON coordinator_panes;
CREATE POLICY coordinator_panes_service_only ON coordinator_panes FOR ALL TO service_role USING (true);

-- Migration tracker.
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260712220000', '024_coordinator_panes', ARRAY[
  'CREATE TABLE coordinator_panes (PK agent_id, pane_text, captured_at) — cross-host coordinator pane snapshots for console peek, zero SSH',
  'RLS deny-all + REVOKE PUBLIC/anon/authenticated (service-role-only substrate table)'
]) ON CONFLICT (version) DO NOTHING;
