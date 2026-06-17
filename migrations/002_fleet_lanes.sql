-- fleet_lanes — INTENT/CONFIG registry for CC build lanes (CAI-RESP-255 #4).
-- Target project: tscuymavysscrvoberrr (orch substrate, NOT prod ceayjeamtmcyzzvqflus).
--
-- DESIGN INVARIANT (the whole point of challenge #4): this table is DECLARATIVE
-- INTENT only — what lanes SHOULD exist and how to boot them. It holds NO
-- self-reported liveness (no pid, no last_seen, no status, no heartbeat). Those
-- drift the instant a process dies without writing back. Liveness is DERIVED ON
-- READ by lanes.sh from the OS: tmux session presence + `.lane.lock(pid)` in the
-- worktree (kill -0). Registry = desired_state; reconciler compares against
-- actual-derived. Never store actual here.

create table if not exists fleet_lanes (
  lane           text primary key,                       -- stable lane name (e.g. 'mirror'); lanes.sh keys off this
  worktree_path  text not null,                          -- absolute dir the lane runs in
  branch         text,                                   -- git branch the lane sits on; NULL for non-code nodes
  model          text not null default 'claude-opus-4-8',
  launcher       text not null default 'launch_dangerous_cc.sh'  -- boot script; strategic node uses boot_cai.sh
                   check (launcher in ('launch_dangerous_cc.sh', 'boot_cai.sh')),
  base_agent_id  text references agents(id),             -- family (engineer lanes) or exact id (strategic); informational
  desired_state  text not null default 'up'
                   check (desired_state in ('up', 'down', 'paused')),
  notes          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
alter table fleet_lanes enable row level security;
create policy "service role full access" on fleet_lanes
  using (true) with check (true);
create index if not exists idx_fleet_lanes_desired on fleet_lanes(desired_state);

-- Seed: settled intent only.
--  'mirror' — mirrors lanes.sh's current hardcoded LANES block (cc-ihsanos Lane C).
--  'cai'    — strategic node; launcher RATIFIED as boot_cai.sh (CAI-RESP-256 Part B,
--             RE #2347). Seeded desired_state='down': it is NOT lanes.sh-managed and is
--             booted by the OPERATOR via boot_cai.sh after his sign-off on the autonomy
--             model (CAI-RESP-255 #3). 'down' guarantees no reconciler ever auto-boots it.
insert into fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes) values
  ('mirror',
   '/Users/sheikhmusa/.config/superpowers/worktrees/ihsanos/mirror',
   'feature/mirror/v0.1',
   'claude-opus-4-8',
   'launch_dangerous_cc.sh',
   'cc-ihsanos',
   'up',
   'WordPress site-clone toolchain (CAI-WEB-CLONE-001/002/003); writes into wordpress-sites repo. Single-writer: requests schema from Lane B, never authors migrations.'),
  ('cai',
   '/Users/sheikhmusa/wingmen/wingmen-cai',
   null,
   'claude-opus-4-8',
   'boot_cai.sh',
   'cai',
   'down',
   'Perpetual strategic node running AS agent_id=cai (singleton, no sub-tag). Launcher boot_cai.sh ratified CAI-RESP-256 Part B. Operator-booted only, after autonomy sign-off (CAI-RESP-255 #3); not lanes.sh-managed.')
on conflict (lane) do nothing;
