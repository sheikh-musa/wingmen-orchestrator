-- Run this in Supabase → SQL Editor for the new orchestrator project

-- Jobs queue
create table jobs (
  id bigint generated always as identity primary key,
  repo_name text not null,
  description text not null,
  status text not null default 'queued',
    -- queued | running | completed | failed | paused
    -- + (ORCHESTRATOR-STATUS-001): pushed | pr_open | push_failed | pr_failed
    -- + (ORCHESTRATOR-STATUS-001 Option B, cc-ihsanos): deploy_verification_pending
    -- NOTE: no CHECK constraint on jobs.status today; cc-ihsanos's Batch 1
    -- migration is expected to add one covering the combined enum above.
  priority int not null default 5,
  fail_count int not null default 0,
  session_prompt text,
  result_summary text,
  triggered_by text default 'telegram',
  retry_after timestamptz,                          -- Constraint 5: rate-limit backoff, null = no backoff
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
    -- cc-ihsanos Batch 1 migration (ORCHESTRATOR-STATUS-001 coordination):
    -- must expand this list to include 'pushed', 'pr_open',
    -- 'push_failed', 'pr_failed'. Agent-side writes from
    -- wingmen_orch._apply_publisher_result depend on it. Until then,
    -- 'deployed' retains its (known-incorrect) meaning of "agent done".
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

-- Shared-bot storefront: merchant slug carried by t.me/dookanabot?start=<slug>
-- Must equal a valid ihsanos organizations.slug (cross-lane contract, msg #1913).
alter table clients add column if not exists storefront_slug text;
create unique index if not exists clients_storefront_slug_key
  on clients (storefront_slug)
  where storefront_slug is not null;

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
create index if not exists qa_findings_created_at_idx on qa_findings(created_at desc);

-- Link bug_reports back to qa_findings + auto-fix tier
alter table bug_reports add column if not exists qa_finding_id bigint references qa_findings(id);
alter table bug_reports add column if not exists auto_fix_tier int;
  -- 1 = auto-approve (high confidence), 2 = fast-track (medium), 3 = full review (low/null)

alter table bug_reports add column if not exists severity text check (severity in ('critical', 'high', 'medium', 'low'));
create index if not exists idx_bug_reports_severity on bug_reports(severity);

alter table bug_reports add column if not exists prev_diagnosis text;

-- Work outputs (structured CC build results for CAI visibility)
create table if not exists work_outputs (
  id bigint generated always as identity primary key,
  job_id bigint not null references jobs(id),
  repo_name text not null,
  build_spec text,
  commit_sha text,
  files_changed text[],
  diff_summary text,
  deploy_url text,
  cc_output_summary text,
  test_passed boolean,
  success boolean not null default false,
  gate1_result jsonb,   -- ARCH-021: commit existence check result
  gate2_result jsonb,   -- ARCH-021: intent alignment (Haiku) result
  created_at timestamptz not null default now()
);
alter table work_outputs enable row level security;
create policy "service role full access" on work_outputs
  using (true) with check (true);
create index idx_work_outputs_job on work_outputs(job_id);
create index idx_work_outputs_repo on work_outputs(repo_name);
create index idx_work_outputs_created on work_outputs(created_at desc);

-- ARCH-004: polled by strategic_decisions_poll.py
create table if not exists strategic_decisions (
  id bigint generated always as identity primary key,
  decision_ref text not null,
  title text not null,
  decision text,
  reasoning text,
  repos_affected text[],
  source text not null,
    -- claude_ai_session | claude_code_proposal | musa_direct
  challenge_status text,
    -- accepted | cai_review_requested | challenge_window
  bypass_review boolean default false,
  notified_at timestamptz,
  execution_status text,
    -- NULL (pending) | implemented | failed
  completed_job_id bigint,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);
alter table strategic_decisions enable row level security;
create policy "service role full access" on strategic_decisions
  using (true) with check (true);
create index idx_strategic_decisions_ref on strategic_decisions(decision_ref);
create index idx_strategic_decisions_source on strategic_decisions(source);
create index idx_strategic_decisions_created on strategic_decisions(created_at desc);
alter table strategic_decisions add column if not exists category text
  check (category in ('governance', 'performance', 'qa', 'product', 'infra', 'pricing', 'islamic', 'operations'));
alter table strategic_decisions add column if not exists parent_ref text;
create index if not exists idx_strategic_decisions_category on strategic_decisions(category) where category is not null;
create index if not exists idx_strategic_decisions_parent on strategic_decisions(parent_ref) where parent_ref is not null;
-- SUBSTRATE-COHERENCE-001 D (cai #2001): lifecycle status; 'archived' is first-class.
alter table strategic_decisions add column if not exists status text default 'active';
alter table strategic_decisions drop constraint if exists strategic_decisions_status_check;
alter table strategic_decisions add constraint strategic_decisions_status_check
  check (status = any (array['active', 'superseded', 'under_review', 'archived']));

-- ARCH-007: notification dedup + cai visibility
create table if not exists notification_log (
  id bigint generated always as identity primary key,
  source text not null,
    -- strategic_decisions_poll | strategic_decisions_complete | cai_review_request | job_progress | job_complete
  decision_ref text,
  channel text default 'telegram',
  recipient text,
  message_text text,
  telegram_msg_id text,
  dedup_key text unique,
  created_at timestamptz not null default now()
);
alter table notification_log enable row level security;
create policy "service role full access" on notification_log
  using (true) with check (true);
create index idx_notification_log_dedup on notification_log(dedup_key) where dedup_key is not null;
create index idx_notification_log_decision on notification_log(decision_ref) where decision_ref is not null;
create index idx_notification_log_created on notification_log(created_at desc);

-- Semantic drift audits (LLM review of spec vs actual output)
create table if not exists drift_audits (
  id bigint generated always as identity primary key,
  job_id bigint not null references jobs(id),
  repo_name text not null,
  build_spec text,
  actual_output text,
  verdict text not null check (verdict in ('aligned', 'minor_drift', 'major_drift')),
  reasoning text not null,
  drift_details text,
  created_at timestamptz not null default now()
);
alter table drift_audits enable row level security;
create policy "service role full access" on drift_audits
  using (true) with check (true);
create index idx_drift_audits_job on drift_audits(job_id);
create index idx_drift_audits_verdict on drift_audits(verdict);
create index idx_drift_audits_created on drift_audits(created_at desc);

-- Claude Code work session narratives (BUG-006)
create table if not exists cc_work_sessions (
  id bigint generated always as identity primary key,
  job_id bigint not null references jobs(id),
  repo_name text not null,
  triggered_by text,
  session_prompt text,
  narrative text not null,
  outcome text not null check (outcome in ('success', 'failed', 'crashed')),
  duration_seconds int,
  files_changed text[],
  commit_sha text,
  deploy_url text,
  created_at timestamptz not null default now()
);
alter table cc_work_sessions enable row level security;
create policy "service role full access" on cc_work_sessions
  using (true) with check (true);
create index idx_cc_work_sessions_job on cc_work_sessions(job_id);
create index idx_cc_work_sessions_repo on cc_work_sessions(repo_name);
create index idx_cc_work_sessions_created on cc_work_sessions(created_at desc);

-- ARCH-016: Bug pipeline readiness tracker (phase gates)
create table if not exists bug_pipeline_readiness (
  id bigint generated always as identity primary key,
  phase text not null default 'phase_0',
  gate_ref text not null unique,
  gate_title text not null,
  status text not null default 'pending'
    check (status in ('pending', 'green', 'yellow', 'red', 'flagged')),
  days_clean int not null default 0,
  last_breach_at timestamptz,
  last_breach_reason text,
  required_days_clean int not null default 14,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table bug_pipeline_readiness enable row level security;
create policy "service role full access" on bug_pipeline_readiness
  using (true) with check (true);
create index idx_bug_pipeline_readiness_phase on bug_pipeline_readiness(phase);
create index idx_bug_pipeline_readiness_status on bug_pipeline_readiness(status);

-- Seed: phase_0 gates (idempotent)
insert into bug_pipeline_readiness (phase, gate_ref, gate_title, status, notes) values
  ('phase_0', 'silent_queue_stall',     'Zero silent queue stalls >30min',                           'pending', 'Needs TASK-034 detector'),
  ('phase_0', 'zombie_running_cleanup', 'Zombie "running" row auto-cleanup on orchestrator startup', 'pending', 'Needs TASK-033; today 4 zombies accumulated from restart'),
  ('phase_0', 'swallowed_except',       'Swallowed-except blocks have counters + escalation',        'pending', 'Needs TASK-035 harness'),
  ('phase_0', 'fire_drill_migration',   'Fire drill: broken migration escalates not ships',          'green',   'ARCH-015 schema_gate — verify under TASK-037'),
  ('phase_0', 'fire_drill_sigkill',     'Fire drill: SIGKILL mid-CLI leaves no zombies on restart', 'pending', 'Depends on TASK-033 + TASK-037'),
  ('phase_0', 'fire_drill_conflict',    'Fire drill: concurrent conflicting bugs serialize cleanly', 'pending', 'TASK-037'),
  ('phase_0', 'fire_drill_cli_timeout', 'Fire drill: CLI timeout with partial work → WIP branch + pause', 'pending', 'TASK-037'),
  ('phase_0', 'fire_drill_missing_tool','Fire drill: test tool absent → pause not crash',           'pending', 'TASK-037'),
  ('phase_0', 'tests_move_with_code',   'Behavior changes ship with matching test updates',          'green',   'ARCH-014 spec rule; verify no stale-test stalls for 14 days'),
  ('phase_0', 'paused_job_escalation',  'Paused jobs escalate at 1h/6h',                            'green',   'TASK-028 shipped')
on conflict (gate_ref) do nothing;
-- TASK-031: Archive tables for completed jobs and terminal decisions
-- jobs_archive: 90-day retention (execution history, not durable record)
-- strategic_decisions_archive: 365-day minimum retention (institutional memory)

create table if not exists jobs_archive (
  id bigint primary key,                          -- original job id (not auto-generated)
  repo_name text not null,
  description text not null,
  status text not null,
  priority int not null default 5,
  fail_count int not null default 0,
  session_prompt text,
  result_summary text,
  triggered_by text,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  archived_at timestamptz not null default now()
);
alter table jobs_archive enable row level security;
create policy "service role full access" on jobs_archive
  using (true) with check (true);
create index idx_jobs_archive_repo on jobs_archive(repo_name);
create index idx_jobs_archive_status on jobs_archive(status);
create index idx_jobs_archive_archived on jobs_archive(archived_at desc);
create index idx_jobs_archive_created on jobs_archive(created_at desc);

create table if not exists strategic_decisions_archive (
  id bigint primary key,                          -- original decision id (not auto-generated)
  decision_ref text not null,
  title text not null,
  decision text,
  reasoning text,
  repos_affected text[],
  source text,
  challenge_status text,
  bypass_review boolean,
  notified_at timestamptz,
  execution_status text,
  completed_job_id bigint,
  completed_at timestamptz,
  category text,
  parent_ref text,
  created_at timestamptz not null,
  archived_at timestamptz not null default now()
);
alter table strategic_decisions_archive enable row level security;
create policy "service role full access" on strategic_decisions_archive
  using (true) with check (true);
create index idx_sda_ref on strategic_decisions_archive(decision_ref);
create index idx_sda_challenge_status on strategic_decisions_archive(challenge_status);
create index idx_sda_archived on strategic_decisions_archive(archived_at desc);
create index idx_sda_created on strategic_decisions_archive(created_at desc);

-- ── repo_snapshot (ARCH-024: evidence layer) ─────────────────────────────────
create table if not exists repo_snapshot (
  id               bigint generated always as identity primary key,
  repo_name        text not null,
  commit_sha       text not null,
  commit_timestamp timestamptz not null,
  branch           text not null default 'main',
  total_loc        int,
  file_count       int,
  test_count       int,
  migration_count  int,
  route_count      int,
  top_level_dirs   jsonb,
  recent_churn     jsonb,
  dependencies     jsonb,
  schema_tables    text[],
  schema_rls_policies int,
  created_at       timestamptz not null default now()
);
alter table repo_snapshot enable row level security;
create policy "service role full access" on repo_snapshot using (true) with check (true);
create index if not exists idx_repo_snapshot_repo_commit on repo_snapshot(repo_name, commit_timestamp desc);
create index if not exists idx_repo_snapshot_sha on repo_snapshot(commit_sha);

-- ARCH-024: evidence columns on strategic_decisions
alter table strategic_decisions add column if not exists evidence_commit_sha text;
alter table strategic_decisions add column if not exists evidence_deploy_id text;
alter table strategic_decisions add column if not exists evidence_test_run_id text;
create index if not exists idx_sd_evidence_commit on strategic_decisions(evidence_commit_sha) where evidence_commit_sha is not null;

-- ── boot_briefing view (ARCH-019: lightweight index, ARCH-024: +repo_snapshot) ─
-- Returns <10KB: decision refs+titles only, no decision/reasoning body.
-- Full content via get_decision(ref) / get_repo_context(key) RPC functions.
create or replace view boot_briefing as
select 'repo_context'::text as source, rc.repo as key,
    json_build_object('phase', rc.current_phase, 'blockers', rc.blockers,
        'test_health', rc.test_health, 'updated_at', rc.updated_at) as context
from repo_context rc
union all
select 'repo_snapshot'::text as source, rs.repo_name as key,
    json_build_object('commit_sha', left(rs.commit_sha, 8), 'commit_timestamp', rs.commit_timestamp,
        'branch', rs.branch, 'file_count', rs.file_count, 'total_loc', rs.total_loc,
        'test_count', rs.test_count, 'migration_count', rs.migration_count,
        'route_count', rs.route_count, 'schema_tables', rs.schema_tables) as context
from (select distinct on (repo_name) repo_name, commit_sha, commit_timestamp, branch,
        file_count, total_loc, test_count, migration_count, route_count, schema_tables
    from repo_snapshot order by repo_name, commit_timestamp desc) rs
union all
select 'active_decision'::text as source, sd.decision_ref as key,
    json_build_object('title', left(sd.title, 80), 'domain', sd.domain,
        'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source,
        'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status,
        'decided_at', sd.decided_at) as context
from strategic_decisions sd where sd.status = 'active'
union all
select 'open_qa_failure'::text as source,
    ((((qf.repo || '/') || qf.role) || '/') || qf.flow) as key,
    json_build_object('role', qf.role, 'flow', qf.flow, 'error', qf.error,
        'found_at', qf.found_at) as context
from qa_findings qf where qf.status = 'fail' and qf.resolved_at is null
union all
select 'latest_cc_session'::text as source, sub.repo_name as key,
    json_build_object('narrative', left(sub.narrative, 500), 'outcome', sub.outcome,
        'commit_sha', sub.commit_sha, 'created_at', sub.created_at) as context
from (select distinct on (cws.repo_name) cws.repo_name, cws.narrative, cws.outcome,
        cws.commit_sha, cws.created_at
    from cc_work_sessions cws order by cws.repo_name, cws.created_at desc) sub
union all
select 'latest_digest'::text as source, dig.title as key,
    json_build_object('topics', dig.topics_covered, 'open_questions', dig.open_questions,
        'action_items', dig.action_items, 'session_date', dig.session_date) as context
from (select sd.session_date, sd.title, sd.topics_covered, sd.open_questions, sd.action_items
    from session_digests sd order by sd.created_at desc limit 1) dig;

-- get_decision(ref): full decision body on demand
create or replace function get_decision(ref text)
returns table (decision_ref text, title text, decision text, reasoning text,
    domain text, category text, repos_affected text[], challenge_status text,
    execution_status text, source text, decided_at timestamptz, decided_by text,
    parent_ref text)
language sql stable security definer as $$
    select decision_ref, title, decision, reasoning, domain, category,
        repos_affected, challenge_status, execution_status, source,
        decided_at, decided_by, parent_ref
    from strategic_decisions where decision_ref = ref limit 1;
$$;

-- ── ecosystem_audit_log (ARCH-023) ───────────────────────────────────────────
create table if not exists ecosystem_audit_log (
  id            bigserial primary key,
  gate_name     text not null,
  ran_at        timestamptz not null default now(),
  dry_run       boolean not null default true,
  rows_affected int not null default 0,
  actions_taken jsonb not null default '[]',
  notes         text,
  created_at    timestamptz not null default now()
);
alter table ecosystem_audit_log enable row level security;
create policy "service role full access" on ecosystem_audit_log
  using (true) with check (true);
create index if not exists idx_eal_gate_name on ecosystem_audit_log(gate_name);
create index if not exists idx_eal_ran_at on ecosystem_audit_log(ran_at desc);

-- get_repo_context(key): full repo context on demand (recent_changes, known_debt, etc.)
create or replace function get_repo_context(key text)
returns table (repo text, current_phase text, blockers text[], recent_changes text,
    known_debt text[], test_health text, deploy_url text,
    architecture_summary text, updated_at timestamptz)
language sql stable security definer as $$
    select repo, current_phase, blockers, recent_changes, known_debt,
        test_health, deploy_url, architecture_summary, updated_at
    from repo_context where repo = key limit 1;
$$;

-- BUG-035 / CAI-RESP-205: reconciliation primitive for cross-agent BLOCKING
-- handoffs. Each blocker gets a substrate row with an id; rulings reference it
-- via strategic_decisions.unblocks_task_id; reconciled_at is set by an explicit
-- owner close (never auto-stamped on ruling-existence — that is the read !=
-- reconciled bug). Applied via scripts/apply_migration.py (historical applier:
-- apply_blocking_tasks_schema.py, deleted 2026-09-05 PR #88; no numbered
-- migration file — inline DDL predates that convention).
create table if not exists blocking_tasks (
  id bigint generated always as identity primary key,
  owner_agent text not null,
  created_by text not null,
  subject text not null,
  detail text,
  thread_id uuid,
  status text not null default 'open'
    check (status in ('open', 'reconciled', 'cancelled')),
  created_at timestamptz not null default now(),
  reconciled_at timestamptz,
  reconciled_by_decision_ref text,
  is_test boolean not null default false
);
alter table strategic_decisions
  add column if not exists unblocks_task_id bigint references blocking_tasks(id);

-- Blocker view: open cross-agent tasks, excluding test traffic. ruling_issued
-- distinguishes "ruling delivered but not reconciled" (chase signal) from
-- "still waiting on cai". Surfaced as the boot_briefing open_blocking_task arm.
create or replace view open_blocking_tasks as
select bt.id, bt.owner_agent, bt.created_by, bt.subject, bt.detail,
       bt.thread_id, bt.created_at, bt.is_test,
       sd.decision_ref as unblocking_ruling_ref,
       (sd.decision_ref is not null) as ruling_issued,
       (now() - bt.created_at) as age
from blocking_tasks bt
left join strategic_decisions sd on sd.unblocks_task_id = bt.id
where bt.status = 'open' and bt.is_test is not true;
