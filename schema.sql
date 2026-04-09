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

-- Extend clients table for bug pipeline
alter table clients add column if not exists telegram_username text;
alter table clients add column if not exists email text;
alter table clients add column if not exists platform text default 'unknown';
  -- platform: ihsanos, cosem, wordpress, custom, unknown
alter table clients add column if not exists repo_name text;
alter table clients add column if not exists ihsanos_org_id text;
alter table clients add column if not exists capabilities text[] default '{}';

-- Bug reports pipeline
create table bug_reports (
  id uuid primary key default gen_random_uuid(),
  client_id bigint references clients(id),
  reporter_name text not null,
  reporter_email text,
  reporter_source text not null check (reporter_source in ('telegram', 'web')),
  auth_provider text check (auth_provider in ('supabase', 'firebase', 'telegram', 'none')),
  repo_name text not null,
  description text not null,
  screenshot_url text,
  page_url text,
  status text not null default 'new' check (status in (
    'new', 'diagnosing', 'proposed', 'approved', 'deploying',
    'deployed', 'verified', 'rejected', 'escalated', 'still_broken'
  )),
  confidence text check (confidence in ('high', 'medium', 'low')),
  root_cause text,
  affected_files text[],
  proposed_diff text,
  diagnosis_full text,
  approval_message_id text,
  approval_sent_to text[],
  approver_id text,
  approved_by text,
  rejection_reason text,
  retry_count int not null default 0,
  job_id bigint references jobs(id),
  deploy_url text,
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);
alter table bug_reports enable row level security;
create policy "service role full access" on bug_reports
  using (true) with check (true);

create index idx_bug_reports_status on bug_reports(status);
create index idx_bug_reports_repo on bug_reports(repo_name);
create index idx_bug_reports_client on bug_reports(client_id);
create index idx_bug_reports_created on bug_reports(created_at desc);