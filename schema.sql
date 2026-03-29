-- Run this in Supabase → SQL Editor for the new orchestrator project

-- Jobs queue
create table jobs (
  id bigint generated always as identity primary key,
  repo_name text not null,
  description text not null,
  status text not null default 'queued',
    -- queued | running | completed | failed | paused
  priority int not null default 5,
  fail_count int not null default 0,
  session_prompt text,
  result_summary text,
  triggered_by text default 'telegram',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table jobs enable row level security;
create policy "service role full access" on jobs
  using (true) with check (true);

-- Build log
create table build_log (
  id bigint generated always as identity primary key,
  job_id bigint references jobs(id),
  repo_name text not null,
  phase text not null,
  message text not null,
  level text not null default 'info', -- info | warn | error
  created_at timestamptz not null default now()
);
alter table build_log enable row level security;
create policy "service role full access" on build_log
  using (true) with check (true);

-- Repo memory (persistent context per repo)
create table repo_memory (
  id bigint generated always as identity primary key,
  repo_name text not null,
  key text not null,
  value text not null,
  created_at timestamptz not null default now(),
  unique(repo_name, key)
);
alter table repo_memory enable row level security;
create policy "service role full access" on repo_memory
  using (true) with check (true);

-- Clients (future client-facing tier)
create table clients (
  id bigint generated always as identity primary key,
  name text not null,
  telegram_chat_id text,
  plan text not null default 'basic',
  active boolean not null default true,
  created_at timestamptz not null default now()
);
alter table clients enable row level security;
create policy "service role full access" on clients
  using (true) with check (true);
