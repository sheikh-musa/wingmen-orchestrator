"""Single source of truth for the Execution-Reliability Layer DDL.

The authored migration (`supabase/migrations/20260717_exec_reliability_layer.sql`)
and the test fixtures BOTH render their SQL from here so the two can never drift.
`schema` is parameterised only so the test-suite can materialise an isolated,
self-cleaning throwaway schema on the substrate WITHOUT touching the real
`public.exec_work_items` table (the migration is authored-unapplied).

Design invariants encoded in this DDL (spec §7 traceability):
  EXEC-1  grant_ref FK -> strategic_decisions(decision_ref) — a work-item cannot
          exist without an authorizing grant row.
  EXEC-2  idempotency_key UNIQUE — enqueue + delivery dedupe.
  EXEC-3  state enum includes 'bounced' — fail-closed terminal state.
  EXEC-4  claimed_by/lease_expires_at + lease_scope[] + a constrained `exec_runner`
          DB role that has NO write on strategic_decisions (cannot flip
          execution_status) and can only touch rows it owns (RLS WITH CHECK).
  EXEC-5  pre_verify_result / post_proof jsonb for the money/irreversible path.
"""
from __future__ import annotations

# execution_status='granted' is the ONLY value that authorizes work (EXEC-1).
# The runner NEVER writes this column (it lives on strategic_decisions and the
# exec_runner role has no UPDATE there). It is read-only to this whole layer.
GRANTED = "granted"

# States a decision may be in and still count as "settled / not-revoked" when the
# runner re-checks the grant each cycle (spec §3.2). Anything else -> bounce.
SETTLED_EXECUTION_STATUS = ("granted", "implemented", "ip_gate_cleared")
# strategic_decisions.status values that mean the decision is dead/withdrawn.
REVOKED_STATUS = ("superseded", "archived")

WORK_STATES = (
    "pending",
    "claimed",
    "running",
    "done",
    "failed",
    "skipped",
    "bounced",
)


def work_items_ddl(schema: str = "public") -> str:
    """DDL for the durable work-item queue (spec §1)."""
    return f"""
create table if not exists {schema}.exec_work_items (
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
    -- also dedupes deliveries against {schema}.exec_delivery_ledger by this key.
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
    on {schema}.exec_work_items (created_at)
    where state = 'pending';
create index if not exists exec_work_items_grant_idx
    on {schema}.exec_work_items (grant_ref);
create index if not exists exec_work_items_lease_idx
    on {schema}.exec_work_items (lease_expires_at)
    where state in ('claimed','running');

create or replace function {schema}.exec_work_items_bump_updated_at()
    returns trigger language plpgsql as $bump$
    begin new.updated_at = now(); return new; end;
$bump$;
drop trigger if exists trg_exec_work_items_updated_at on {schema}.exec_work_items;
create trigger trg_exec_work_items_updated_at
    before update on {schema}.exec_work_items
    for each row execute function {schema}.exec_work_items_bump_updated_at();
"""


def delivery_ledger_ddl(schema: str = "public") -> str:
    """DDL for the relay delivery ledger (spec §5 / CAI-464 c1).

    A row here is the durable proof that a NOTICE was delivered. The relay checks
    this ledger BEFORE sending and records AFTER sending; the UNIQUE idempotency
    key makes a double-record impossible and a concurrent double-send collapse to
    one. The crash-after-send-before-record window re-delivers (a duplicate) —
    never a silent drop.
    """
    return f"""
create table if not exists {schema}.exec_delivery_ledger (
    id               bigint generated always as identity primary key,
    idempotency_key  text not null unique,     -- EXEC-2: mirrors the work-item key
    work_item_id     bigint,
    channel          text not null,
    target           text not null,
    provider_msg_id  text,                      -- e.g. bus msg id / telegram id
    delivered_at     timestamptz not null default now()
);
"""


def rls_and_role_sql(schema: str = "public", role: str = "exec_runner") -> str:
    """RLS + the constrained runner DB role (EXEC-4).

    The runner connects as `{role}` (a NOLOGIN group role here — the hub wires a
    concrete login role that INHERITs it, and sets `app.current_agent_id` at
    session start). By construction this role:
      * has NO INSERT/UPDATE/DELETE on strategic_decisions  -> cannot flip
        execution_status, cannot write a grant/decision;
      * has NO INSERT on exec_work_items                     -> cannot mint work
        (only the service-role enqueue path creates authorized work — EXEC-1);
      * may only SELECT + UPDATE exec_work_items rows it owns (RLS WITH CHECK);
      * may append to the delivery ledger (relay records deliveries).
    Service role keeps full access (poller/enqueue/safety-net run as service role
    and BYPASSRLS), matching the repo's existing RLS convention.
    """
    return f"""
do $role$
begin
    if not exists (select 1 from pg_roles where rolname = '{role}') then
        create role {role} nologin;
    end if;
end
$role$;

-- Runner authority: read grants (to re-check), never write them (EXEC-4).
revoke all on public.strategic_decisions from {role};
grant select on public.strategic_decisions to {role};

-- Runner may claim + update work items and record deliveries, never mint work.
revoke all on {schema}.exec_work_items from {role};
grant select, update on {schema}.exec_work_items to {role};
revoke all on {schema}.exec_delivery_ledger from {role};
grant select, insert on {schema}.exec_delivery_ledger to {role};

alter table {schema}.exec_work_items enable row level security;
drop policy if exists exec_work_items_service_all on {schema}.exec_work_items;
create policy exec_work_items_service_all on {schema}.exec_work_items
    to service_role using (true) with check (true);

-- Runner can see claimable + its own rows; can only leave a row owned by itself.
drop policy if exists exec_work_items_runner_select on {schema}.exec_work_items;
create policy exec_work_items_runner_select on {schema}.exec_work_items
    for select to {role}
    using (true);
drop policy if exists exec_work_items_runner_update on {schema}.exec_work_items;
create policy exec_work_items_runner_update on {schema}.exec_work_items
    for update to {role}
    using (state = 'pending' or claimed_by = current_setting('app.current_agent_id', true))
    with check (claimed_by = current_setting('app.current_agent_id', true));

alter table {schema}.exec_delivery_ledger enable row level security;
drop policy if exists exec_delivery_ledger_service_all on {schema}.exec_delivery_ledger;
create policy exec_delivery_ledger_service_all on {schema}.exec_delivery_ledger
    to service_role using (true) with check (true);
drop policy if exists exec_delivery_ledger_runner_ins on {schema}.exec_delivery_ledger;
create policy exec_delivery_ledger_runner_ins on {schema}.exec_delivery_ledger
    for insert to {role} with check (true);
drop policy if exists exec_delivery_ledger_runner_sel on {schema}.exec_delivery_ledger;
create policy exec_delivery_ledger_runner_sel on {schema}.exec_delivery_ledger
    for select to {role} using (true);
"""


def full_migration_sql(schema: str = "public", role: str = "exec_runner") -> str:
    """The complete, ordered DDL body (no BEGIN/COMMIT — caller wraps)."""
    return (
        work_items_ddl(schema)
        + delivery_ledger_ddl(schema)
        + rls_and_role_sql(schema, role)
    )
