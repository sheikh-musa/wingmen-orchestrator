-- 120_post_agent_message_optional_params.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: dropped public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text)
-- assert: no_execute public public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text)
-- assert: search_path public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text)
--
-- backlog#124 (orch-console ruling, bus #74939): the 3 scripts held back from the
-- one-shots migration (lane_drill_seed.py, nervous_system/qwen_lane.py,
-- opus_reprobe_storefront.py) can't express what their raw INSERT sets today --
-- is_test=true (the first two) and a fixed posted_by_identity literal (the third) --
-- through migration 117's 9-param post_agent_message. Console's ruling: EXTEND the
-- RPC with both as OPTIONAL, DEFAULTED trailing params rather than leave these 3 on
-- a permanent raw-INSERT bypass (a permanent bypass would mean migration 119's revoke
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
-- SEQUENCING (flagged explicitly to orch-console, bus #74939 reply): this migration
-- must apply to the live substrate BEFORE migration 118 (118's
-- `ALTER FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text)
-- OWNER TO bus_writer` targets the OLD 9-param signature, which this migration drops --
-- 118's text has been updated in the SAME PR to target the new 11-param signature
-- instead, on the assumption 120 lands first). mark_delivered/ack_message are
-- untouched by this migration; 118's ALTER FUNCTION statements for those two are
-- unaffected. No ownership change happens here: DROP+CREATE naturally leaves the new
-- function owned by whichever role applies this migration (the same admin/superuser
-- role that applied 117 originally) -- 118 is still what moves ownership to
-- bus_writer, just against the new signature.
--
-- TDD'd against an isolated local Postgres, never this substrate (tests/test_post_
-- agent_message_optional_params.sql -- see tests/test_post_agent_message_optional_
-- params.py): proves (1) a 9-positional-arg call still resolves unambiguously and
-- behaves exactly as before, (2) p_is_test=true sets is_test on the row, (3) an
-- explicit p_posted_by_identity is honored (not overridden by the trigger), (4)
-- omitting it still lets the trigger auto-populate current_user exactly as today.

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
    'Durable acked inbox posting RPC (migration 117), extended (migration 120, '
    'backlog#124) with 2 optional trailing params so is_test/posted_by_identity '
    'callers can move off raw INSERT without a lossy behavior change. A 9-arg call '
    'defaults both to the exact pre-120 behavior.';

REVOKE EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) TO service_role;
