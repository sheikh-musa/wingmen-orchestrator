-- CAI-RESP-304 follow (lint PR#50 finding): post_journal_atomic (049 ledger, substrate) is a financial WRITE RPC
-- but was anon/authenticated-exec (only-revokes-PUBLIC footgun). Lock to service_role.
REVOKE EXECUTE ON FUNCTION public.post_journal_atomic(jsonb, bigint, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.post_journal_atomic(jsonb, bigint, uuid) TO service_role;
