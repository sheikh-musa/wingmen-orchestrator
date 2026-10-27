-- 122_post_agent_message_optional_params.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: dropped public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text)
-- assert: no_execute public public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text)
-- assert: no_execute anon public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text)
-- assert: no_execute authenticated public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text)
-- assert: search_path public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text)
--
-- Musa op#34845, bus #78531: the applier (apply_migration.py) verifies privilege
-- claims AFTER the SQL runs, because a REVOKE can silently no-op (exactly finding D
-- below -- the FROM PUBLIC revoke never touched anon/authenticated's own default-ACL
-- grant). The no_execute public assert alone would have passed even with finding D's
-- bug still live (PUBLIC was genuinely revoked); only the anon/authenticated asserts
-- added here actually prove the fix at apply time instead of trusting the REVOKE line.
--
-- backlog#124 (orch-console ruling, bus #74939): the 3 scripts held back from the
-- one-shots migration (lane_drill_seed.py, nervous_system/qwen_lane.py,
-- opus_reprobe_storefront.py) can't express what their raw INSERT sets today --
-- is_test=true (the first two) and a fixed posted_by_identity literal (the third) --
-- through migration 117's 9-param post_agent_message. Console's ruling: EXTEND the
-- RPC with both as OPTIONAL, DEFAULTED trailing params rather than leave these 3 on
-- a permanent raw-INSERT bypass (a permanent bypass would mean migration 124's revoke
-- can never safely apply to them -- defeats the whole migration).
--
-- WHY DROP + CREATE, NOT CREATE OR REPLACE: Postgres identifies a function by name
-- *and* parameter list -- CREATE OR REPLACE FUNCTION post_agent_message(<11 params>)
-- would NOT replace the existing 9-param function, it would create a SECOND,
-- separate overload alongside it. A 9-positional-arg call would then be AMBIGUOUS
-- between "the 9-param function" and "the 11-param function with its last 2
-- defaulted" -- Postgres raises "function is not unique", breaking every
-- already-migrated caller (fleet_stall_watch.py, bus_send.py itself, etc; console's
-- own hard requirement: "don't break the already-migrated callers"). DROP the exact
-- old 9-param signature first so only ONE function named post_agent_message exists
-- afterward -- an existing 9-arg positional call then resolves unambiguously to this
-- function with its 2 new trailing params defaulted, exactly reproducing today's
-- behavior for every caller that doesn't pass them.
--
-- Defaults chosen to be the exact current behavior for a caller that omits them:
--   p_is_test            BOOLEAN DEFAULT FALSE  -- agent_messages.is_test's own column default
--   p_posted_by_identity  TEXT    DEFAULT NULL   -- NULL lets the pre-existing BEFORE INSERT
--                                                    trigger (populate_agent_messages_provenance,
--                                                    migrations/infra/bedrock_substrate_core.sql)
--                                                    auto-populate it from current_user exactly as
--                                                    it does today for every direct-INSERT and
--                                                    RPC caller that never sets this column --
--                                                    passing a non-NULL value here (as
--                                                    opus_reprobe_storefront.py's raw INSERT does
--                                                    today with a fixed literal) is honored as-is,
--                                                    since the trigger's guard is
--                                                    `IF NEW.posted_by_identity IS NULL`.
--
-- SEQUENCING / RENUMBERED TWICE (PR#490 CI findings, backlog#124): this file was
-- originally numbered 120 (applying AFTER the bus_writer-enforcement migration,
-- then numbered 118) with an explicit "must apply BEFORE 118 on the live
-- substrate" header note -- that silently broke CI's automated bootstrap, which
-- replays migrations/*.sql in plain filename order and has no notion of a
-- manual override, so the enforcement migration ran first against the
-- then-still-9-param function and failed. First renumber: to 118 (enforcement/
-- revoke shifted to 119/120) so filename order IS dependency order. Second
-- renumber (orch-console bus #76518, found on GO-gate verify): the base branch
-- fable/substrate-safe-fixes had separately merged its OWN, unrelated
-- migrations/118_operator_asks_needs_musa.sql in the meantime -- a logical
-- number collision (same ordinal, two different migrations) even though git
-- reported the merge as clean (the filenames differed, so no text conflict).
-- Rebased onto that base and shifted this whole set again: this file 118->119,
-- enforcement 119->120, revoke 120->121.
--
-- THIRD RENUMBER (orch-console bus #78234, found on GO-gate verify): by the time this
-- PR rebased again, fable/substrate-safe-fixes had independently taken 119/120/121 for
-- 3 unrelated migrations (console_status_board_grant, held_commitments_console_ro_policy,
-- operator_ask_turnaround). Shifted the whole set a third time: this file 119->122,
-- enforcement 120->123, revoke 121->124. Content is otherwise unchanged from the
-- original: migration 123 (the enforcement migration, formerly 118, then 119, then
-- 120)'s `ALTER FUNCTION post_agent_message(text,text,text,text,text,text,
-- boolean,uuid,text) OWNER TO bus_writer` targets the OLD 9-param signature,
-- which THIS migration drops, and 123's text already targets the new 11-param
-- signature instead. mark_delivered/ack_message are untouched by this
-- migration; 123's ALTER FUNCTION statements for those two are unaffected. No
-- ownership change happens here: DROP+CREATE naturally leaves the new function
-- owned by whichever role applies this migration (the same admin/superuser
-- role that applied 117 originally) -- 123 is still what moves ownership to
-- bus_writer, just against the new signature.
--
-- TDD'd against an isolated local Postgres, never this substrate (tests/test_post_
-- agent_message_optional_params.sql -- see tests/test_post_agent_message_optional_
-- params.py): proves (1) a 9-positional-arg call still resolves unambiguously and
-- behaves exactly as before, (2) p_is_test=true sets is_test on the row, (3) an
-- explicit p_posted_by_identity is honored (not overridden by the trigger), (4)
-- omitting it still lets the trigger auto-populate current_user exactly as today.
--
-- EXECUTE-grant side effect (bus #78352 finding #4, reviewed and acknowledged -- not a
-- silent accident): dropping the old 9-param function also drops ANY EXECUTE grant on
-- that exact signature -- Postgres privileges are tied to a specific (name, parameter
-- list) pair, not the function name alone, so DROP FUNCTION takes its grants down with
-- it; the new 11-param function below starts with nothing explicit until the REVOKE/
-- GRANT lines after CREATE FUNCTION run. See those lines' own comment for bus #78369
-- finding D -- the real, deeper version of this same concern: a standing Supabase
-- default-privileges rule re-grants anon/authenticated on the NEW function regardless
-- of what the old one had, and the caller census done for it.

DROP FUNCTION IF EXISTS post_agent_message(text,text,text,text,text,text,boolean,uuid,text);
DROP FUNCTION IF EXISTS post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text);

CREATE FUNCTION post_agent_message(
    p_from_agent          TEXT,
    p_to_agent            TEXT,
    p_message_type        TEXT,
    p_subject             TEXT,
    p_body                TEXT,
    p_priority            TEXT,
    p_requires_response   BOOLEAN,
    p_thread_id           UUID,
    p_sub_tag             TEXT,
    p_is_test             BOOLEAN DEFAULT FALSE,
    p_posted_by_identity  TEXT DEFAULT NULL
) RETURNS TABLE(id BIGINT, thread_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
BEGIN
    RETURN QUERY
    INSERT INTO agent_messages
        (from_agent, to_agent, message_type, subject, body,
         priority, requires_response, thread_id, sub_tag, is_test, posted_by_identity)
    VALUES
        (p_from_agent, p_to_agent, p_message_type, p_subject, p_body,
         p_priority, p_requires_response, p_thread_id, p_sub_tag, p_is_test, p_posted_by_identity)
    RETURNING agent_messages.id, agent_messages.thread_id;
END;
$fn$;

COMMENT ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) IS
    'Durable acked inbox posting RPC (migration 117), extended (migration 122, '
    'backlog#124) with 2 optional trailing params so is_test/posted_by_identity '
    'callers can move off raw INSERT without a lossy behavior change. A 9-arg call '
    'defaults both to the exact pre-122 behavior.';

-- Musa op#34845, bus #78369 finding D (verified on the live substrate): REVOKE ... FROM
-- PUBLIC alone is NOT enough. Supabase's own bootstrap sets a standing
-- `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon,
-- authenticated, service_role` -- this grants each of those roles its OWN individual
-- EXECUTE entry on every function at CREATE time, a separate ACL entry from PUBLIC's.
-- DROP + CREATE (above) makes a brand-new function object, which picks up that standing
-- rule immediately -- so without the explicit REVOKE below, anon and authenticated keep
-- their own grant regardless of the PUBLIC revoke, and this SECURITY DEFINER RPC stays
-- reachable with the anon key. Caller census (bus #78352 finding #4): grepped the
-- entire ~/wingmen/orchestrator repo, all worktrees, for any PostgREST-style RPC call
-- that could reach this function with an anon/authenticated key -- zero matches, with
-- the caveat that an external, inaccessible codebase (e.g. a browser app holding a
-- Supabase anon key) cannot be ruled out from here. This REVOKE closes the gap either
-- way, independent of whatever that census did or didn't find.
REVOKE EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) TO service_role;
