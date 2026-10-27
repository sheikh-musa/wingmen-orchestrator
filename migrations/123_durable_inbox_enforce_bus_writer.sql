-- 123_durable_inbox_enforce_bus_writer.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_execute anon public.mark_delivered(bigint,integer)
-- assert: no_execute authenticated public.mark_delivered(bigint,integer)
-- assert: no_execute anon public.ack_message(bigint)
-- assert: no_execute authenticated public.ack_message(bigint)
--
-- LIVE HOLE IN SCOPE (Musa op#34845, bus #78531, console's own live query on
-- tscuymavysscrvoberrr): mark_delivered(bigint,integer)/ack_message(bigint) are
-- SECURITY DEFINER; before this fix, anon and authenticated held EXECUTE on both via
-- Supabase's standing ALTER DEFAULT PRIVILEGES rule (same mechanism as migration 122's
-- finding D on post_agent_message) -- migration 117's own `REVOKE EXECUTE ... FROM
-- PUBLIC` never touched it, since PUBLIC is not anon/authenticated as individual roles.
-- That meant anyone holding the public anon key could mark ANY bus row delivered or
-- acked via PostgREST. This file's REVOKE below (plus the asserts above, which the
-- applier checks AFTER the SQL runs so a silent no-op REVOKE can't pass) is the fix.
-- service_role keeps its EXECUTE (migration 117's original grant, untouched here --
-- it's the only legitimate caller path, though no in-repo script calls either RPC yet;
-- grep confirms zero callers in scripts/ or nervous_system/ outside tests/).
-- This file DOES now contain a REVOKE (triggering the applier's assert-required
-- convention), unlike migration 124 (the separate, still-deferred PR-B revoke of
-- service_role/postgres direct INSERT on the table itself).
--
-- PR SPLIT (Musa op#34845, bus #78393 ruling, superseding the plan below of "124 lands
-- once the writer gate is green"): migration 124 does NOT ship in this PR. orch-console's
-- own live query found postgres is rolsuper=FALSE but rolbypassrls=TRUE -- so a plain
-- REVOKE INSERT on this table's owner (postgres) IS a real, revocable ACL entry (my
-- earlier design proposal wrongly assumed ownership was an unconditional bypass; it is
-- not -- only RLS bypass is unconditional here, via BYPASSRLS, and only for roles that
-- have it). That makes 124 a real guardrail once it revokes from BOTH postgres and
-- service_role, but it ships only as its own later PR-B, gated on a full writer inventory
-- across 4 categories (python psycopg, .sh/psql heredocs, PostgREST/service-key REST
-- calls, and 6 postgres-owned DB functions that INSERT into this table directly) -- none
-- of which is complete yet. This migration (123) is unaffected: it was always additive,
-- never revoking anything, so it ships now regardless of 124's timeline.
--
-- RENUMBERED THREE TIMES (PR#490 CI findings, backlog#124): first 118->119, then
-- (orch-console bus #76518: base branch separately merged its own, unrelated
-- migrations/118_operator_asks_needs_musa.sql -- a logical number collision)
-- 119->120, then (orch-console bus #78234: base branch independently took
-- 119/120/121 for 3 unrelated migrations) 120->123. See
-- 122_post_agent_message_optional_params.sql's header -- this file now applies
-- AFTER that one (filename order), which is what its own content always
-- required; only the number changed.
--
-- Durable acked inbox, additive half of the final step (backlog#124, Tier-1(b) janky-
-- tools audit op#33149; design: bus #74850/#74858/#74860, orch-console approved).
-- PURELY ADDITIVE, on purpose (orch-console ruling, bus #74885): creates the dedicated
-- bus_writer role and moves the three posting RPCs onto it, but does NOT revoke
-- service_role's direct INSERT -- that is migration 124, a separate file, gated on
-- the fleet-wide enforce-in-code check reporting zero bypasses (bus #74880/#74885).
-- This migration's own prior draft bundled the revoke in THIS file; split out after
-- review found that bundling meant the only thing stopping an accidental apply of the
-- breaking half was a comment, not structure -- a routine apply-latest or the DDL
-- coverage watchdog could fire it. Splitting makes this migration safe to apply any
-- time once merged: service_role keeps direct INSERT until 124 lands.
--
-- HARD CONSTRAINT paid for by an earlier, fully-rejected draft (bus #74738/#74741):
-- a SECURITY DEFINER RPC owned by the role being revoked dies with the revoke. Here the
-- owner is a DISTINCT, dedicated NOLOGIN role (bus_writer) -- never service_role --
-- so migration 124's later revoke cannot self-defeat the RPCs.
--
-- Grant footprint beyond the obvious INSERT, each one caught by TDD against an
-- isolated local Postgres (none of these would have been obvious from reasoning about
-- the grant model alone):
--   * SELECT (id, thread_id) ON agent_messages -- post_agent_message's
--     `RETURNING agent_messages.id, agent_messages.thread_id` requires SELECT on the
--     returned columns, NOT just INSERT on the table -- a real Postgres RETURNING
--     rule, not something either review pass caught; only a real INSERT-then-fetch
--     test run as the actual role surfaces it (has_table_privilege(..., 'INSERT')
--     alone reports true and is NOT sufficient proof).
--   * SELECT, INSERT, UPDATE ON work_item + INSERT ON work_item_audit -- the
--     AFTER-trigger work_item_mirror_bus_trg (and work_item_mirror() itself) are
--     SECURITY INVOKER, not DEFINER, so they execute AS bus_writer once the posting
--     RPCs are owned by it; service_role has these only via Supabase's blanket
--     default-ACL (migrations 039/049), which does not extend to a brand-new role
--     (bus #74860/#74863 design review finding).
--   * UPDATE (delivered_at, lease_expires_at, acked_at) ON agent_messages -- a direct
--     consequence of putting ALL THREE RPCs under bus_writer (bus #74863 answer to
--     Q1, "consistency over minimalism"): mark_delivered/ack_message UPDATE those
--     columns, so their new owner needs UPDATE on them too, not just the INSERT this
--     migration's headline is about. Caught by TDD (test_mark_delivered_and_ack_
--     unaffected_by_enforcement), not by reasoning about the ownership decision alone.
--   * SELECT (from_agent) ON agent_messages (Musa op#34845, bus #78352 finding #3) --
--     work_item_derive_bus's "`responded_at IS NOT NULL` AND response_ref looks numeric"
--     branch does `SELECT from_agent FROM agent_messages WHERE id=p_response_ref::bigint`.
--     That branch is reachable AS bus_writer: work_item_mirror_bus_trg is SECURITY
--     INVOKER and fires on ANY UPDATE of a requires_response row, including
--     mark_delivered/ack_message's UPDATEs -- if a row's responded_at was already set
--     by an earlier, unrelated UPDATE before bus_writer's ack/deliver call lands, the
--     trigger re-fires AS bus_writer with NEW.responded_at already non-NULL. Without
--     this grant the SELECT fails, and the trigger's own exception handler (bus #73195)
--     swallows it as a WARNING -- a silent mirror failure, never a loud one. Caught
--     by a dedicated regression test, not by re-reading the ownership decision alone.
--
-- RLS (Musa op#34845, bus #78352, NO-GO at head 0a64d3e -- real finding, not a false
-- positive): see the policy block below. Column-level grants are necessary but not
-- sufficient once row level security is enabled on the table, which live agent_messages
-- already has. Without a matching policy, bus_writer's grants above are moot -- every
-- RPC write would be denied on the live substrate despite passing every local/CI test,
-- because no local/CI Postgres cluster had RLS enabled to surface this. The fixture now
-- mirrors live RLS permanently so this class of gap is caught going forward.
--
-- Safe to merge and apply as soon as this is green -- it changes nothing about who can
-- already write to agent_messages, only adds a second, narrower-privileged path.
--
-- SEQUENCING (migration 122, backlog#124, bus #74939): post_agent_message's
-- signature below targets the 11-param version migration 122 creates, not the
-- original 9-param one from 117. Filename order (117, 122, 123, 124) now IS
-- dependency order -- 122 applies before this file, so the 9-param signature is
-- already gone by the time this ALTER FUNCTION line runs. If this migration is
-- ever applied BEFORE 122 for any reason, this line will fail to resolve (the
-- 9-param function still exists then) -- that failure is a correct, loud signal
-- to apply 122 first, not a bug to silently work around.
--
-- LOCK-WAIT GUARD (Musa op#34845, bus #78689, console's own live rehearsal on
-- tscuymavysscrvoberrr, PR#490 gate): CREATE POLICY on agent_messages below takes an
-- ACCESS EXCLUSIVE lock, and the applier sets no lock_timeout of its own -- PGOPTIONS
-- does not pass through Supavisor (console measured lock_timeout reading 0 there).
-- Console's rehearsal hit a real session that had been idle-in-transaction for ~3 days
-- holding a lock on agent_messages; a real apply in that state would queue forever
-- behind it, stalling every bus read and write fleet-wide, not just this migration.
-- This MUST be this file's first executed statement (see
-- test_123_sets_lock_timeout_as_its_first_statement) -- same convention already used
-- by migrations 080/082/084/085/091/092/118/119/120/121.
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bus_writer') THEN
        CREATE ROLE bus_writer NOLOGIN;
    END IF;
END $$;

-- Musa op#34845, bus #78576 (console's live repro on PG17.11, NOT GO on head 471296d):
-- live's applying role (postgres) is rolsuper=FALSE, rolcreaterole=TRUE, with
-- createrole_self_grant empty. Since PG16, a CREATEROLE (non-superuser) role that
-- creates another role gets ADMIN on it automatically but NOT SET, unless
-- createrole_self_grant includes 'set' -- live's is empty. Without SET, the ALTER
-- FUNCTION ... OWNER TO bus_writer lines below fail with "must be able to SET ROLE
-- bus_writer" and the whole migration transaction rolls back. This GRANT gives the
-- applying role SET explicitly (it already holds ADMIN automatically, so it may grant
-- itself SET); INHERIT FALSE on purpose, so the applying role does not silently gain
-- bus_writer's privileges in its ambient session -- it must explicitly SET ROLE to act
-- as bus_writer, same as any other caller would.
GRANT bus_writer TO CURRENT_USER WITH SET TRUE, INHERIT FALSE;

-- PostgreSQL's own rule for ALTER ... OWNER TO (any object, any version): the new
-- owner role must hold CREATE on the object's schema, "so altering the owner doesn't
-- do anything you couldn't do by dropping and recreating the object." Without this,
-- the ALTER FUNCTION ... OWNER TO lines below fail with "permission denied for schema
-- public" -- caught by the same live-shape test fixture as the SET-ROLE fix above
-- (Musa op#34845, bus #78576), a second real gap that error surfaced once the first
-- (SET ROLE) was fixed. This does widen bus_writer's footprint slightly (it could now
-- create new objects in public), which is an unavoidable consequence of a non-
-- superuser NOLOGIN role owning schema-resident functions at all, not something a
-- narrower grant can substitute for.
GRANT CREATE ON SCHEMA public TO bus_writer;

GRANT INSERT ON agent_messages TO bus_writer;
GRANT SELECT (id, thread_id, from_agent) ON agent_messages TO bus_writer;
GRANT UPDATE (delivered_at, lease_expires_at, acked_at) ON agent_messages TO bus_writer;
GRANT USAGE ON SEQUENCE agent_messages_id_seq TO bus_writer;
GRANT SELECT, INSERT, UPDATE ON work_item TO bus_writer;
GRANT INSERT ON work_item_audit TO bus_writer;

-- RLS (Musa op#34845, bus #78352 NO-GO, proven via a repro that enables RLS in the test
-- fixture the way live agent_messages already has it): the live table has row level
-- security ENABLED with policies scoped to service_role/console_readonly/cto_desktop only.
-- Column-level GRANTs alone are necessary but NOT sufficient once RLS is on -- a role with
-- no matching policy is denied regardless of its table/column grants. bus_writer had no
-- policy at all, so EVERY RPC write (post_agent_message's INSERT, mark_delivered/
-- ack_message's UPDATEs) would be silently denied the instant this migration's OWNER TO
-- took effect on the live substrate -- this never showed up locally because this fixture,
-- like every other local/CI Postgres cluster, never had RLS enabled to begin with.
-- Idempotent (CREATE POLICY has no IF NOT EXISTS) via the same pg_catalog-guard shape as
-- the role creation above. Scoped to exactly the 3 operations bus_writer's column grants
-- already allow -- USING/WITH CHECK (true) because row-level filtering was never the
-- point here (every row in this table is fleet-internal), only gating which ROLE may act,
-- which the column grants already do.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'agent_messages'
          AND policyname = 'bus_writer_insert'
    ) THEN
        CREATE POLICY bus_writer_insert ON agent_messages
            FOR INSERT TO bus_writer WITH CHECK (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'agent_messages'
          AND policyname = 'bus_writer_select'
    ) THEN
        CREATE POLICY bus_writer_select ON agent_messages
            FOR SELECT TO bus_writer USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'agent_messages'
          AND policyname = 'bus_writer_update'
    ) THEN
        CREATE POLICY bus_writer_update ON agent_messages
            FOR UPDATE TO bus_writer USING (true) WITH CHECK (true);
    END IF;
END $$;

ALTER FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) OWNER TO bus_writer;
ALTER FUNCTION mark_delivered(bigint,integer) OWNER TO bus_writer;
ALTER FUNCTION ack_message(bigint) OWNER TO bus_writer;

-- Musa op#34845, bus #78531: close the anon/authenticated EXECUTE hole (see header).
-- FROM PUBLIC repeated here too (harmless no-op if migration 117's own REVOKE already
-- holds) so this file is self-contained and correct regardless of 117's state.
-- ORDER/AUTHORITY (Musa op#34845, bus #78576 fix #2 -- REVISED from the originally
-- instructed "REVOKE before OWNER TO"): that literal ordering broke idempotency --
-- on a RE-RUN, bus_writer already owns both functions from the first apply, so a
-- REVOKE run as the applying role (which only has SET on bus_writer, not ownership
-- or GRANT OPTION once transferred) fails with "permission denied for function
-- mark_delivered". Caught by the required re-run test, not by re-reading the
-- instruction alone. Fixed by running the REVOKE explicitly AS bus_writer instead,
-- AFTER the OWNER TO lines above -- unambiguous authority (bus_writer always owns
-- these two functions at this point, both on a fresh apply and a re-run) via a
-- deliberate role switch rather than ordering-dependent ownership timing.
SET ROLE bus_writer;
REVOKE EXECUTE ON FUNCTION mark_delivered(bigint,integer) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION ack_message(bigint) FROM PUBLIC, anon, authenticated;
RESET ROLE;
