-- 054_anon_view_exposure_class_fix.sql
-- cc-storefront's adjacent finding (#23776), and it is the CLASS that 051 only fixed an instance of.
--
-- 051 closed three honesty views that ran as OWNER and were granted to anon, so they bypassed
--   their base tables' RLS. cc-storefront, auditing 049/050/051 on the implementation lens,
--   pointed out the obvious next question and then answered it empirically: the fix was scoped
--   to the three views cc-quality happened to find, NOT to the class. One view over,
--   `boot_briefing` had the identical posture.
--
-- MEASURED AS anon, before this migration:
--     boot_briefing               2052 rows   <- the governance index itself; cc-storefront read
--                                                1336 active decisions, 299 with full reasoning
--                                                text (CAI-RESP-800: decision 4296 chars,
--                                                reasoning 1022)
--     inbox_sla_violations         667 rows
--     agent_message_stale_claims    66 rows
--     fleet_drain_freshness         37 rows
--     finance_burn                   1 row    <- finance_subscriptions
--     fleet_proposal_metrics_v       1 row
--     stale_agents                   1 row
--     open_blocking_tasks            0 rows   <- structurally exposed, empty today
--
-- THE LESSON, and it is cai's own CAI-978 wording turned on me: FIX THE CLASS, NOT THE TEST. I
--   fixed exactly the three views I was handed and did not ask what else had the same shape --
--   the same narrowness cc-quality named when it said its own PR#74/#75 clearances covered the
--   logic and not the posture. Three people have now made this mistake tonight in three places.
--
-- SO THE DELIVERABLE HERE IS NOT THE LIST OF EIGHT. It is the CHECK in the apply script, which
--   fails if ANY public view is owner-run AND readable by anon/authenticated. A list goes stale
--   the next time someone adds a view; a check does not. That is the difference between this and
--   an inventory, and inventories are what we spent the night discovering rot.
--
-- SAFE, VERIFIED BEFORE APPLYING RATHER THAN AFTER: `console_readonly` holds SELECT on NONE of
--   these eight, so the fleet console does not read any of them and cannot be blinded by
--   security_invoker (the failure 051 introduced and I only caught by reading as the console).
--   Agents read via DATABASE_URL as `postgres`, which is unaffected.
--
-- REACHABILITY, not rounded either way (cc-quality's caveat, still correct here): this is the
--   DB-layer grant posture. Whether an external unauthenticated caller can reach it depends on
--   whether this project's PostgREST exposes `public` and is network-reachable, which none of us
--   has established. The posture is wrong regardless. No breach is claimed.
--
-- APPLY: direct psycopg only -- scripts/apply_anon_view_exposure_class_fix.py.
--   ⚠ NEVER `supabase db push` -- decision 962 is SPECIFICALLY about boot_briefing: the CLI's
--   shadow-diff path re-applies historic CREATE OR REPLACE VIEW bodies and silently strips later
--   arms from this exact view. This migration does not touch any view BODY, only its grants and
--   reloptions, precisely so it cannot be that bug.

ALTER VIEW agent_message_stale_claims  SET (security_invoker = on);
ALTER VIEW boot_briefing               SET (security_invoker = on);
ALTER VIEW finance_burn                SET (security_invoker = on);
ALTER VIEW fleet_drain_freshness       SET (security_invoker = on);
ALTER VIEW fleet_proposal_metrics_v    SET (security_invoker = on);
ALTER VIEW inbox_sla_violations        SET (security_invoker = on);
ALTER VIEW open_blocking_tasks         SET (security_invoker = on);
ALTER VIEW stale_agents                SET (security_invoker = on);

REVOKE ALL ON agent_message_stale_claims  FROM anon, authenticated;
REVOKE ALL ON boot_briefing               FROM anon, authenticated;
REVOKE ALL ON finance_burn                FROM anon, authenticated;
REVOKE ALL ON fleet_drain_freshness       FROM anon, authenticated;
REVOKE ALL ON fleet_proposal_metrics_v    FROM anon, authenticated;
REVOKE ALL ON inbox_sla_violations        FROM anon, authenticated;
REVOKE ALL ON open_blocking_tasks         FROM anon, authenticated;
REVOKE ALL ON stale_agents                FROM anon, authenticated;

COMMENT ON VIEW boot_briefing IS
    'The three-tier boot index (CLAUDE.md): repo_context summaries, decision refs + titles, open '
    'QA failures, latest session snippets. Read it as `postgres` via DATABASE_URL. '
    'security_invoker=on + no anon/authenticated grant (054): before that it ran with the OWNER''s '
    'privileges and was anon-readable, exposing 1336 active decisions including full reasoning '
    'text -- the same defect 051 fixed on three other views, found one view over by cc-storefront. '
    '⚠ NEVER apply changes to this view via `supabase db push` (decision 962): the shadow-diff '
    'path re-applies historic view bodies and silently strips later arms from THIS view.';
