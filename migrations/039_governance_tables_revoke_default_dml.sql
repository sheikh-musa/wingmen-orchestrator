-- 039_governance_tables_revoke_default_dml.sql
--
-- WHY (CAI-RESP-764, 2026-08-07): the bus DB's ALTER DEFAULT PRIVILEGES on schema
-- public auto-grants service_role + authenticated FULL DML on EVERY new table.
-- The 037/038 security tables inherited it, so despite RLS + explicit SELECT-only
-- grants, service_role (which has bypassrls=true) can WRITE them:
--   * protected_agents / reaper_actors gate admin_mark_offline's kill-authority —
--     a service_role-key holder could remove the hub from protection then reap it,
--     or add a live lane to protection to shield a real ghost (exactly the threat
--     COND-1 named);
--   * admin_offline_audit is the reap audit trail — service_role could erase it.
-- fleet_reaper stays SELECT-only (protected_agents/reaper_actors) + INSERT-only
-- (admin_offline_audit), so the SECDEF reaper still cannot tamper its own
-- protection — COND-1's core held; this closes only the trusted-tier exposure.
--
-- Found by the cc-fleet-health CAI-763 post-apply gate via EFFECTIVE-grant
-- inspection (role_table_grants), which the byte-level source-verify missed
-- (it read 038's GRANT statements, not the inherited default privileges).
--
-- REVOKE-FIRST (before cutover completes): cutover makes admin_mark_offline the
-- ACTIVE writer of admin_offline_audit, so harden the audit against tamper BEFORE
-- real reap rows land. 038 stays applied — the flaw is the pre-existing schema
-- default-privilege, not 038 logic.
--
-- DOCTRINE (cai CAI-764): every future security/governance table on this bus DB
-- must carry a REVOKE like this (or live in a schema without the default grant).
--
-- Does NOT touch agent_status (the substrate's service_role model, out of scope).
-- SCOPE: bus/coordination silo tscuymavysscrvoberrr. Apply via cai §6.6 guarded
-- path (orch-console executor, never db push). All statements run as the table
-- owner (postgres) — no role switching needed.

BEGIN;

-- Kill-authority tables -> DML to the postgres owner ONLY. SELECT kept (readers:
-- fleet_reaper for the fn, console_readonly/service_role for visibility).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.protected_agents FROM service_role, authenticated, anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.reaper_actors    FROM service_role, authenticated, anon;

-- Audit trail -> APPEND-ONLY. Revoke erase (UPDATE/DELETE/TRUNCATE) from the
-- reaper/RLS tiers; fleet_reaper keeps INSERT (the fn writes reap rows), readers
-- keep SELECT. An audit the reaper-tier can erase is not an audit.
REVOKE UPDATE, DELETE, TRUNCATE ON public.admin_offline_audit FROM service_role, authenticated, anon;

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6) — CAI-764
-- ===========================================================================
-- Verified by cc-fleet-health before handoff (rolled-back psql; EFFECTIVE grants
-- checked via information_schema.role_table_grants, per your lesson):
--   POST-REVOKE, on protected_agents + reaper_actors: service_role/authenticated/
--   anon have ZERO of INSERT/UPDATE/DELETE/TRUNCATE (SELECT retained where held);
--   on admin_offline_audit: those roles have ZERO of UPDATE/DELETE/TRUNCATE
--   (append-only); fleet_reaper still holds its SELECT (governance tables) +
--   INSERT (audit); postgres retains full DML as owner.
-- Idempotent: REVOKE of a privilege a role does not hold is a no-op.
-- LEDGER: repo=orchestrator, migration_name=039_governance_tables_revoke_default_dml.sql,
-- silo_ref=tscuymavysscrvoberrr.
-- SEQUENCE: this REVOKE first -> cutover reap_stale_family + lane_watchdog ->
-- 040 = plain DROP sweep_stale_agents (last, zero-callers re-verified).
-- ===========================================================================
