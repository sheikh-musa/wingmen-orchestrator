-- 056_substrate_stat_statements_and_wider_class.sql
-- cc-storefront #23795, verifying 054 at source and finding the thing 054 could not see.
--
-- IT IS NOT A REGRESSION, AND THE DISTINCTION IS THE WHOLE FINDING. cc-storefront reported the
--   #61 pg_stat_statements revoke as regressed. I verified BOTH databases at source before
--   acting, because "the control we fixed came undone" and "we never checked this one" call for
--   different responses:
--     CEAYJ (the client silo, where #61's revoke was done): 0 PUBLIC/anon grants, anon DENIED.
--       CLEAN. The A3 remediation I observed earlier tonight still holds.
--     SUBSTRATE (this coordination plane): PUBLIC held SELECT on BOTH stat views and
--       **anon read 4,935 rows of query text**.
--   So the control did not rot. It was SCOPED to the silo and the sibling database was never
--   checked -- the same shape as 051 fixing three views when the class had eight, and as #61
--   itself. Third time tonight: fixing the instance in front of you is not fixing the class.
--
-- NO QUERY TEXT WAS READ (CAI-985 item 4, cai's ratified restraint): reading possibly-PII
--   literals in order to demonstrate they are exposable would BE the exposure. Row COUNTS only.
--   I therefore do NOT claim what was in those 4,935 rows -- only that anon could read them, and
--   that pg_stat_statements on a coordination plane carries operator and agent message text.
--
-- ALREADY APPLIED as a live REVOKE at 2026-08-16 ~22:35Z before this file existed -- it was a
--   live anon-readable exposure and tightening-only/reversible DDL is pre-authorised (cai's
--   standing line). Recorded here so it is reproducible and so a fresh database gets it too.
--   Measured as a change: anon 4,935 rows -> DENIED on both objects; `postgres` still reads
--   4,937, so every agent's tooling is unaffected.

REVOKE ALL ON extensions.pg_stat_statements      FROM PUBLIC, anon, authenticated;
REVOKE ALL ON extensions.pg_stat_statements_info FROM PUBLIC, anon, authenticated;
