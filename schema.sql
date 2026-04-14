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

-- Bot heartbeat (written every poll cycle, read by ihsanOS super admin)
create table if not exists bot_heartbeat (
  id bigint generated always as identity primary key,
  service text not null,  -- 'orchestrator', 'cto_bot', 'brain_sync'
  status text not null default 'healthy',  -- healthy, degraded, down
  last_ping timestamptz not null default now(),
  metadata jsonb not null default '{}',
  -- metadata: { uptime_seconds, active_jobs, pending_bugs, last_brain_sync, version }
  unique(service)
);
alter table bot_heartbeat enable row level security;
create policy "service role full access" on bot_heartbeat
  using (true) with check (true);

-- ═══════════════════════════════════════════════════════════════
-- White-Label Bot System
-- ═══════════════════════════════════════════════════════════════

-- Extend clients table for bot support
alter table clients add column if not exists telegram_bot_token text;
alter table clients add column if not exists bot_username text;
alter table clients add column if not exists bot_display_name text;
alter table clients add column if not exists personality text;
alter table clients add column if not exists welcome_message text;

-- Bot users (team members + customers per client bot)
create table if not exists bot_users (
  id bigint generated always as identity primary key,
  client_id bigint not null references clients(id),
  telegram_chat_id text not null,
  telegram_username text,
  name text not null,
  role text not null default 'customer' check (role in ('owner', 'manager', 'staff', 'customer')),
  permissions text[] default '{}',
  invite_code text,
  invite_expires_at timestamptz,
  status text not null default 'active' check (status in ('pending', 'active', 'deactivated')),
  added_by bigint references bot_users(id),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique(client_id, telegram_chat_id)
);
alter table bot_users enable row level security;
create policy "service role full access" on bot_users
  using (true) with check (true);
create index idx_bot_users_client on bot_users(client_id);
create index idx_bot_users_chat_id on bot_users(telegram_chat_id);
create index idx_bot_users_invite on bot_users(invite_code) where invite_code is not null;

-- Bot conversations (state machine for multi-turn flows)
create table if not exists bot_conversations (
  id bigint generated always as identity primary key,
  client_id bigint not null references clients(id),
  telegram_chat_id text not null,
  flow text not null,  -- 'ordering', 'qurban_booking', 'site_edit', 'bug_report', 'team_manage'
  step text not null,
  state_data jsonb not null default '{}',
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  unique(client_id, telegram_chat_id)
);
alter table bot_conversations enable row level security;
create policy "service role full access" on bot_conversations
  using (true) with check (true);
create index idx_bot_conversations_lookup on bot_conversations(client_id, telegram_chat_id);
create index idx_bot_conversations_expires on bot_conversations(expires_at) where expires_at is not null;

-- Client groups (per-client Telegram group linkage)
create table if not exists client_groups (
  id bigint generated always as identity primary key,
  client_id bigint not null references clients(id),
  group_chat_id text not null,
  group_name text,
  group_type text default 'group',  -- group | supergroup
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique(client_id, group_chat_id)
);
alter table client_groups enable row level security;
create policy "service role full access" on client_groups
  using (true) with check (true);
create index idx_client_groups_client on client_groups(client_id);
create index idx_client_groups_chat on client_groups(group_chat_id);

-- ═══════════════════════════════════════════════════════════════
-- QA Findings Ingestion Pipeline
-- ═══════════════════════════════════════════════════════════════

create table if not exists qa_findings (
  id bigint generated always as identity primary key,
  repo_name text not null,
  source text not null check (source in ('ci', 'e2e', 'lighthouse', 'manual', 'sentry')),
  severity text not null default 'medium' check (severity in ('critical', 'high', 'medium', 'low')),
  title text not null,
  description text not null,
  page_url text,
  screenshot_url text,
  raw_output text,
  status text not null default 'new' check (status in ('new', 'bridged', 'ignored', 'duplicate')),
  bug_report_id uuid references bug_reports(id),
  created_at timestamptz not null default now()
);
alter table qa_findings enable row level security;
create policy "service role full access" on qa_findings
  using (true) with check (true);
create index idx_qa_findings_status on qa_findings(status);
create index idx_qa_findings_repo on qa_findings(repo_name);
create index idx_qa_findings_created on qa_findings(created_at desc);

-- Link bug_reports back to qa_findings + auto-fix tier
alter table bug_reports add column if not exists qa_finding_id bigint references qa_findings(id);
alter table bug_reports add column if not exists auto_fix_tier int;
  -- 1 = auto-approve (high confidence), 2 = fast-track (medium), 3 = full review (low/null)

alter table bug_reports add column if not exists severity text check (severity in ('critical', 'high', 'medium', 'low'));
create index if not exists idx_bug_reports_severity on bug_reports(severity);

alter table bug_reports add column if not exists prev_diagnosis text;