"""BUG-024 Phase 2 — agent_messages / strategic_decisions identity enforcement.

Hardens the provenance triggers to SECURITY INVOKER + OVERWRITE (caller input
ignored), adds RLS INSERT enforcement, seeds the operator allowlist, and VOIDs
all pre-existing (untrustworthy) verified flags. cai #2064: build + test now;
APPLY is operator-gated on per-agent credential provisioning. Never
`supabase db push` to prod (CLAUDE.md decision-962); this is the direct
psycopg-apply path (dry-run default, --apply commits).

SECURITY INVOKER is load-bearing: the existing triggers are SECURITY DEFINER
(owner postgres), so inside them current_user resolves to the owner, not the
caller — posted_by_identity would be stamped 'postgres' while RLS (never
DEFINER) sees the real caller, and the two would disagree. INVOKER makes the
trigger resolve current_user in the same caller context RLS evaluates, so the
stamp and the enforcement agree. On the legacy shared path this is
behavior-neutral (the connection role is already postgres).

Usage:
  python scripts/apply_bug024_identity_enforcement.py          # dry-run
  python scripts/apply_bug024_identity_enforcement.py --apply  # commit
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

HARDEN_AGENT_MESSAGES_TRIGGER = """
create or replace function public.populate_agent_messages_provenance()
returns trigger language plpgsql
security invoker
set search_path to public, pg_temp
as $fn$
begin
  -- OVERWRITE: caller input ignored. Identity is the authenticated caller
  -- (jwt agent_id claim when present, else the Postgres role).
  new.posted_by_identity := coalesce(
    current_setting('request.jwt.claims', true)::json ->> 'agent_id',
    current_user);
  new.from_agent_verified := (
    new.from_agent = new.posted_by_identity
    or exists (select 1 from identity_allowlist
               where posted_by = new.posted_by_identity
                 and allowed_from_agent = new.from_agent));
  return new;
end $fn$;
"""

HARDEN_STRATEGIC_DECISIONS_TRIGGER = """
create or replace function public.populate_strategic_decisions_provenance()
returns trigger language plpgsql
security invoker
set search_path to public, pg_temp
as $fn$
begin
  new.posted_by_identity := coalesce(
    current_setting('request.jwt.claims', true)::json ->> 'agent_id',
    current_user);
  new.decided_by_verified := (
    new.decided_by = new.posted_by_identity
    or exists (select 1 from identity_allowlist
               where posted_by = new.posted_by_identity
                 and allowed_from_agent = new.decided_by));
  return new;
end $fn$;
"""

SEED_ALLOWLIST = """
insert into identity_allowlist (posted_by, allowed_from_agent, note) values
  ('musa', 'cai',  'operator posts ratifications as cai'),
  ('musa', 'musa', 'operator own posts')
on conflict do nothing;
"""

# Each constant is a SINGLE statement. psycopg3 execute() runs one statement
# per call (extended protocol), so multi-statement strings would silently run
# only their first statement. The flat MIGRATION list below preserves order.

DROP_RLS_AGENT_MESSAGES = (
    'drop policy if exists "from_agent must match posting identity" on agent_messages;'
)
CREATE_RLS_AGENT_MESSAGES = """
create policy "from_agent must match posting identity" on agent_messages
  for insert with check (
    from_agent = coalesce(
      current_setting('request.jwt.claims', true)::json ->> 'agent_id',
      current_user)
    or exists (select 1 from identity_allowlist
               where posted_by = coalesce(
                       current_setting('request.jwt.claims', true)::json ->> 'agent_id',
                       current_user)
                 and allowed_from_agent = from_agent));
"""

# SELECT visibility for non-BYPASSRLS per-agent roles: an agent sees its own
# inbox (to_agent = identity), its own sent messages (from_agent = identity,
# needed for INSERT ... RETURNING and BUG-035 reconciliation read-backs), and
# broadcasts. service_role bypasses RLS, so this does not change the legacy path.
# Cross-thread / governance-wide reads stay on the service_role path. Grounded in
# the actual poller filters (every reader uses `to_agent = <self>`).
DROP_RLS_SELECT_AGENT_MESSAGES = (
    'drop policy if exists "agents read own inbox, sent, and broadcasts" on agent_messages;'
)
CREATE_RLS_SELECT_AGENT_MESSAGES = """
create policy "agents read own inbox, sent, and broadcasts" on agent_messages
  for select using (
    to_agent = coalesce(
      current_setting('request.jwt.claims', true)::json ->> 'agent_id',
      current_user)
    or from_agent = coalesce(
      current_setting('request.jwt.claims', true)::json ->> 'agent_id',
      current_user)
    or to_agent = 'broadcast');
"""

DROP_RLS_STRATEGIC_DECISIONS = (
    'drop policy if exists "decided_by must match posting identity" on strategic_decisions;'
)
CREATE_RLS_STRATEGIC_DECISIONS = """
create policy "decided_by must match posting identity" on strategic_decisions
  for insert with check (
    decided_by = coalesce(
      current_setting('request.jwt.claims', true)::json ->> 'agent_id',
      current_user)
    or exists (select 1 from identity_allowlist
               where posted_by = coalesce(
                       current_setting('request.jwt.claims', true)::json ->> 'agent_id',
                       current_user)
                 and allowed_from_agent = decided_by));
"""

# The void runs with the provenance trigger disabled. strategic_decisions has a
# BEFORE UPDATE provenance trigger that would otherwise recompute the flag the
# void UPDATE touches; agent_messages' trigger is INSERT-only but is disabled
# during its void for symmetry.
VOID = [
    ("disable agent_messages trigger",
     "alter table agent_messages disable trigger trg_agent_messages_provenance;"),
    ("void agent_messages verified flags",
     "update agent_messages set from_agent_verified = null where from_agent_verified is not null;"),
    ("enable agent_messages trigger",
     "alter table agent_messages enable trigger trg_agent_messages_provenance;"),
    ("disable strategic_decisions trigger",
     "alter table strategic_decisions disable trigger trg_strategic_decisions_provenance;"),
    ("void strategic_decisions verified flags",
     "update strategic_decisions set decided_by_verified = null where decided_by_verified is not null;"),
    ("enable strategic_decisions trigger",
     "alter table strategic_decisions enable trigger trg_strategic_decisions_provenance;"),
]

# Order matters: harden functions, seed allowlist, void (triggers disabled), then policies.
MIGRATION = [
    ("harden agent_messages trigger", HARDEN_AGENT_MESSAGES_TRIGGER),
    ("harden strategic_decisions trigger", HARDEN_STRATEGIC_DECISIONS_TRIGGER),
    ("seed identity_allowlist", SEED_ALLOWLIST),
    *VOID,
    ("drop rls agent_messages insert", DROP_RLS_AGENT_MESSAGES),
    ("create rls agent_messages insert", CREATE_RLS_AGENT_MESSAGES),
    ("drop rls agent_messages select", DROP_RLS_SELECT_AGENT_MESSAGES),
    ("create rls agent_messages select", CREATE_RLS_SELECT_AGENT_MESSAGES),
    ("drop rls strategic_decisions insert", DROP_RLS_STRATEGIC_DECISIONS),
    ("create rls strategic_decisions insert", CREATE_RLS_STRATEGIC_DECISIONS),
]


def apply_migration(cur) -> None:
    for _label, sql in MIGRATION:
        cur.execute(sql)


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from agent_messages where from_agent_verified is not null")
        am_before = cur.fetchone()[0]
        cur.execute("select count(*) from strategic_decisions where decided_by_verified is not null")
        sd_before = cur.fetchone()[0]
        print(f"non-null verified BEFORE: agent_messages={am_before} strategic_decisions={sd_before}")
        apply_migration(cur)
        cur.execute("select count(*) from agent_messages where from_agent_verified is not null")
        am_after = cur.fetchone()[0]
        cur.execute("select count(*) from strategic_decisions where decided_by_verified is not null")
        sd_after = cur.fetchone()[0]
        print(f"non-null verified AFTER:  agent_messages={am_after} strategic_decisions={sd_after} "
              "(voided; flags recompute under enforcement going forward)")
        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
