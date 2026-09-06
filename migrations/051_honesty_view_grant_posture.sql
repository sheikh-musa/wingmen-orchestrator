-- 051_honesty_view_grant_posture.sql
-- cc-quality #23761, HIGH: the honesty VIEWS bypassed the table RLS I had just added.
--
-- THE FINDING, and it is a correction of MY OWN reasoning an hour earlier. When I added RLS to
--   `decision_audits` in 049 I also checked the VIEW's grants, saw they matched
--   `lane_tasks_state` and `invariant_registry_state` exactly, and concluded no action.
--   THAT CONCLUSION WAS WRONG, and wrong in an instructive way: matching a peer is only a
--   clearance if the peer is right. All three peers had inherited the same bad default, so
--   "consistent with its peers" meant "consistently exposed". I used similarity as evidence of
--   correctness. It is not.
--
-- WHAT WAS ACTUALLY TRUE, proven by cc-quality empirically rather than argued:
--   * The three *_state views carry anon SELECT + authenticated SELECT/INSERT/UPDATE/DELETE from
--     Supabase's default privileges.
--   * `reloptions` was NULL on all three => security_invoker OFF => a view runs with its OWNER's
--     privileges => RLS on the base table is BYPASSED for anyone who can read the view.
--   * SET ROLE anon; SELECT count(*) FROM decision_audit_state -> 1353 rows, the entire
--     governance board including every verdict and auditor -- while the direct table read was
--     correctly denied. So the RLS added in 049 was real for the table and MOOT for reads.
--   * Same posture on invariant_registry_state (PR #74) and lane_tasks_state (PR #75).
--
-- REACHABILITY, stated honestly and NOT rounded either way: this establishes the DB-layer grant
--   posture. Whether an external unauthenticated caller can reach it depends on whether this
--   project's PostgREST exposes `public` and is network-reachable, which cc-quality could not
--   determine and neither did I. The DB posture is wrong regardless of the answer. Fixing it does
--   not require knowing, and I am not claiming a breach.
--
-- THE FIX: security_invoker=on, which is the structural one -- the view then reads with the
--   CALLER's privileges, so the base-table RLS it was bypassing actually applies, and it closes
--   all three views by construction rather than by remembering to REVOKE on each future view.
--   Grants are ALSO mirrored to the peer tables (anon/authenticated out, console_readonly in) so
--   the posture is right at both layers, not just the one that happens to be checked.
--
-- VERIFIED SAFE FOR THE LIVE CONSOLE BEFORE APPLYING, because this is the class of change that
--   silently breaks a board: repo-wide search finds NO console/app consumer of these three views
--   (only the migrations and their apply scripts, which run as postgres). console_readonly is
--   granted SELECT anyway -- including on `invariant_registry`, which it did NOT hold and which
--   would have made invariant_registry_state unreadable to the console the moment anyone wired
--   it. That grant is the difference between this fix and a future outage.
--
-- ALSO: EXECUTE on the 049/050 helper functions was PUBLIC by default inheritance. Mostly inert
--   (their inner writes run as the CALLER, so anon cannot flip a decision) with ONE that is not
--   inert: `escalate_stale_decision_audits()` is SECURITY DEFINER and INSERTS bus rows, so a
--   PUBLIC EXECUTE on it is a bus-write primitive. Locked.
--
-- APPLY: direct psycopg only -- scripts/apply_honesty_view_grant_posture.py.

-- ---------------------------------------------------------------------------------------------
-- 1. The three honesty views: caller's privileges, peer-mirrored grants.
-- ---------------------------------------------------------------------------------------------

ALTER VIEW decision_audit_state       SET (security_invoker = on);
ALTER VIEW lane_tasks_state           SET (security_invoker = on);
ALTER VIEW invariant_registry_state   SET (security_invoker = on);

REVOKE ALL ON decision_audit_state     FROM anon, authenticated;
REVOKE ALL ON lane_tasks_state         FROM anon, authenticated;
REVOKE ALL ON invariant_registry_state FROM anon, authenticated;

-- The board must stay RENDERABLE -- CAI-RESP-987's criterion 2 is that a "could not verify" is
-- visibly renderable, which is worth nothing if the reader cannot read it. console_readonly is
-- the console's role and is SELECT-only by construction.
GRANT SELECT ON decision_audit_state     TO console_readonly;
GRANT SELECT ON lane_tasks_state         TO console_readonly;
GRANT SELECT ON invariant_registry_state TO console_readonly;

-- security_invoker=on means the view now needs the CALLER to hold the base-table grant.
-- console_readonly held SELECT on lane_tasks, strategic_decisions and decision_audits but NOT on
-- invariant_registry -- so without this line, turning on security_invoker would have converted a
-- silent over-exposure into a silent under-exposure. Measured before applying, not after.
GRANT SELECT ON invariant_registry TO console_readonly;

-- AND THE RLS POLICY, which the GRANT alone does not give. Caught by reading the view through
-- the console's REAL connection (CONSOLE_DB_URL) rather than by checking my own post-condition:
-- invariant_registry has RLS enabled, console_readonly held no policy on it, so after
-- security_invoker=on the board returned 0 ROWS instead of 34 -- a silent under-exposure, the
-- exact opposite failure to the one this migration fixes, introduced BY the fix.
-- My post-condition had asserted has_table_privilege (the GRANT) and called that verified. A
-- grant is permission to ask; a policy is permission to see. Checking the first and reporting
-- the second is the same class of error as everything else found tonight -- I verified the thing
-- I had thought of. The post-condition now READS the views as the console instead.
-- Mirrors strategic_decisions_console_ro / lane_tasks_console_ro exactly.
DROP POLICY IF EXISTS invariant_registry_console_ro ON invariant_registry;
CREATE POLICY invariant_registry_console_ro ON invariant_registry
    FOR SELECT TO console_readonly USING (true);

-- ---------------------------------------------------------------------------------------------
-- 2. Helper functions: no PUBLIC EXECUTE by inheritance.
-- ---------------------------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.decision_audit_actor_norm(text)              FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decision_audit_conflict(text, text)          FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decision_audit_required(text)                FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decision_audit_tier_candidate(text, text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.close_decision_by_audit(text, text)          FROM PUBLIC, anon, authenticated;
-- The one that was NOT inert: SECURITY DEFINER and it INSERTs agent_messages rows.
REVOKE ALL ON FUNCTION public.escalate_stale_decision_audits()             FROM PUBLIC, anon, authenticated;

-- console_readonly reads the board, and the board's labels call these. Read-only helpers only --
-- deliberately NOT close_decision_by_audit or escalate_stale_decision_audits, which write.
GRANT EXECUTE ON FUNCTION public.decision_audit_actor_norm(text)              TO console_readonly;
GRANT EXECUTE ON FUNCTION public.decision_audit_conflict(text, text)          TO console_readonly;
GRANT EXECUTE ON FUNCTION public.decision_audit_required(text)                TO console_readonly;
GRANT EXECUTE ON FUNCTION public.decision_audit_tier_candidate(text, text, text) TO console_readonly;

COMMENT ON VIEW decision_audit_state IS
    'CAI-RESP-987/988: what actually happened to each decision, as opposed to what its status '
    'claims. CLOSED-ON-SILENCE is the honest name for accepted_by_timeout. COULD-NOT-VERIFY is '
    'its own state and is not a pass. AUDIT-STALE is computed from the clock, so it stays true '
    'even if the escalator dies. UNTIERED means nobody judged whether an audit was needed. '
    'security_invoker=on (051): this view reads with the CALLER''s privileges, so the base-table '
    'RLS applies through it -- without that, an anon-readable view over an RLS-protected table '
    'silently returns everything (cc-quality #23761). Read is_audit_closed, never '
    'challenge_status alone.';
