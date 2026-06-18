-- 006: let the read-only console role SELECT agent_messages (RLS carve-out).
--
-- The Fleet Console (CAI-RESP-264) is an approved READ-ONLY viewer of the bus,
-- and console_readonly already holds GRANT SELECT on agent_messages (migration
-- 004). But agent_messages RLS has a single policy ("service role only":
-- auth.role() = 'service_role'), so console_readonly silently saw ZERO rows —
-- the Conversation panel AND the Lanes "current activity" line were blank.
--
-- This adds a SELECT-only policy scoped to the console_readonly role. The role
-- is NOSUPERUSER + default_transaction_read_only=on, so this grants visibility
-- only — never mutation. Bodies are additionally PII-redacted in the API layer.
DROP POLICY IF EXISTS console_readonly_select_messages ON agent_messages;
CREATE POLICY console_readonly_select_messages ON agent_messages
  FOR SELECT TO console_readonly
  USING (true);
