-- Execution-Reliability Layer — exec_work_items + exec_delivery_ledger (op#4711 / CAI-RESP-464)
--
-- AUTHORED-UNAPPLIED. Do NOT `supabase db push` (CLAUDE.md decision-962). The hub
-- applies this via the guarded direct psycopg path:
--     python scripts/apply_exec_reliability_layer.py --expect-ref tscuymavysscrvoberrr --apply
--
-- Grants NOTHING: execution_status is never written by this layer. See
-- nervous_system/exec_reliability/schema.py (single source of this DDL).
--
-- Invariant traceability: EXEC-1 grant_ref FK · EXEC-2 idempotency_key UNIQUE ·
-- EXEC-3 'bounced' state · EXEC-4 exec_runner role has no write on
-- strategic_decisions + RLS WITH CHECK on ownership · EXEC-5 pre_verify/post_proof.

create table if not exists public.exec_work_items (
    id                bigint generated always as identity primary key,
    -- EXEC-1: the authorizing grant. Real FK — strategic_decisions.decision_ref
    -- carries a UNIQUE constraint, so a work-item cannot reference a grant that
    -- does not exist.
    grant_ref         text not null
                        references public.strategic_decisions(decision_ref),
    consumer_type     text not null,           -- MVP: 'relay'
    named_artifact    jsonb not null,          -- EXEC-1: the EXACT thing to run
    -- EXEC-2: dedupe key. Enqueue derives it deterministically from the grant +
    -- artifact so re-enqueuing the same grant is a no-op; the relay consumer
    -- also dedupes deliveries against public.exec_delivery_ledger by this key.
    idempotency_key   text not null unique,
    state             text not null default 'pending'
                        check (state in (
                            'pending','claimed','running',
                            'done','failed','skipped','bounced')),
    -- EXEC-4: borrowed + scoped authority. claimed_by is the runner's OWN
    -- agent_id; lease_expires_at bounds the borrowed authority in time;
    -- lease_scope is the explicit authority scope (repos_affected for repo work,
    -- an explicit non-repo scope token otherwise — CAI-464 c3, never unscoped).
    claimed_by        text,
    claimed_at        timestamptz,
    lease_expires_at  timestamptz,
    lease_scope       text[] not null,
    attempts          int not null default 0,
    max_attempts      int not null default 5,
    last_error        text,
    -- EXEC-5: money/irreversible pre-verify gate result + post-execution proof.
    pre_verify_result jsonb,
    post_proof        jsonb,
    result            jsonb,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

-- Claim scan is FIFO (oldest-first, operator doctrine) over claimable rows.
create index if not exists exec_work_items_claimable_idx
    on public.exec_work_items (created_at)
    where state = 'pending';
create index if not exists exec_work_items_grant_idx
    on public.exec_work_items (grant_ref);
create index if not exists exec_work_items_lease_idx
    on public.exec_work_items (lease_expires_at)
    where state in ('claimed','running');

create or replace function public.exec_work_items_bump_updated_at()
    returns trigger language plpgsql as $bump$
    begin new.updated_at = now(); return new; end;
$bump$;
drop trigger if exists trg_exec_work_items_updated_at on public.exec_work_items;
create trigger trg_exec_work_items_updated_at
    before update on public.exec_work_items
    for each row execute function public.exec_work_items_bump_updated_at();

create table if not exists public.exec_delivery_ledger (
    id               bigint generated always as identity primary key,
    idempotency_key  text not null unique,     -- EXEC-2: mirrors the work-item key
    work_item_id     bigint,
    channel          text not null,
    target           text not null,
    provider_msg_id  text,                      -- e.g. bus msg id / telegram id
    delivered_at     timestamptz not null default now()
);

do $role$
begin
    if not exists (select 1 from pg_roles where rolname = 'exec_runner') then
        create role exec_runner nologin;
    end if;
end
$role$;

-- Runner authority: read grants (to re-check), never write them (EXEC-4).
revoke all on public.strategic_decisions from exec_runner;
grant select on public.strategic_decisions to exec_runner;

-- Runner may claim + update work items and record deliveries, never mint work.
revoke all on public.exec_work_items from exec_runner;
grant select, update on public.exec_work_items to exec_runner;
revoke all on public.exec_delivery_ledger from exec_runner;
grant select, insert on public.exec_delivery_ledger to exec_runner;

alter table public.exec_work_items enable row level security;
drop policy if exists exec_work_items_service_all on public.exec_work_items;
create policy exec_work_items_service_all on public.exec_work_items
    to service_role using (true) with check (true);

-- Runner can see claimable + its own rows; can only leave a row owned by itself.
drop policy if exists exec_work_items_runner_select on public.exec_work_items;
create policy exec_work_items_runner_select on public.exec_work_items
    for select to exec_runner
    using (true);
drop policy if exists exec_work_items_runner_update on public.exec_work_items;
create policy exec_work_items_runner_update on public.exec_work_items
    for update to exec_runner
    using (state = 'pending' or claimed_by = current_setting('app.current_agent_id', true))
    with check (claimed_by = current_setting('app.current_agent_id', true));

alter table public.exec_delivery_ledger enable row level security;
drop policy if exists exec_delivery_ledger_service_all on public.exec_delivery_ledger;
create policy exec_delivery_ledger_service_all on public.exec_delivery_ledger
    to service_role using (true) with check (true);
drop policy if exists exec_delivery_ledger_runner_ins on public.exec_delivery_ledger;
create policy exec_delivery_ledger_runner_ins on public.exec_delivery_ledger
    for insert to exec_runner with check (true);
drop policy if exists exec_delivery_ledger_runner_sel on public.exec_delivery_ledger;
create policy exec_delivery_ledger_runner_sel on public.exec_delivery_ledger
    for select to exec_runner using (true);
