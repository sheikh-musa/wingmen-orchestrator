-- 056 — close the two anon-reachable VIEWS that bypass the RLS migration 055 enabled.
--
-- WHY 055 WAS NOT ENOUGH (cc-quality #24866, and it is right): 055 enabled RLS on five
-- tables and revoked anon/authenticated. A VIEW over such a table with no
-- `security_invoker` executes as its OWNER, so the RLS is never consulted for a read
-- through the view. The table is shut and the view is open.
--
-- MEASURED, effective read as `anon` (SET LOCAL ROLE anon, counts only, no rows pulled):
--   agent_observed_activity  -> READABLE, 37 rows   (cc-fleet-health's new SLA view, mig054)
--   held_commitments_due     -> READABLE,  0 rows   (predicate matches nothing TODAY —
--                                                    latent, not safe; same state as
--                                                    revenue_ledger before 055)
--
-- FIVE OTHER VIEWS ALSO LACK security_invoker AND ARE DELIBERATELY NOT TOUCHED HERE:
--   decision_audit_state, invariant_registry_state  -> anon DENIED; read by `console_readonly`
--   inbox_sla_violations, share_lane_status_v, share_pool_status_v -> anon DENIED, no console grant
-- They are closed by the GRANT layer today. Setting security_invoker on the first two
-- would make the read execute as `console_readonly`, which is NOT bypassrls and has no
-- policies on the underlying tables — i.e. a likely console outage, for zero reachability
-- gain. That cannot be proven safe inside one transaction (a second connection cannot see
-- an uncommitted setting), so it is reported as a residual rather than guessed at:
-- THOSE FIVE REST ON ONE LAYER, THE GRANT. Fixing them needs a console-side verification
-- pass, not a migration written at 07:00Z during an incident.
--
-- PRIOR ART: this is the decision_audit_state shape — stale_blockers and
-- cto_council_open_sessions both carry security_invoker and both return 401/empty to anon.
-- The reachable ones are exactly the ones without it.
--
-- BOTH LAYERS, deliberately: security_invoker makes the view respect RLS, AND the grant is
-- revoked. Either alone would have closed today's reachability; one layer is what produced
-- both this finding and 055's.
--
-- REVERSIBLE: `ALTER VIEW ... RESET (security_invoker)` + re-GRANT. Nothing is dropped.
-- `postgres` and `service_role` are BYPASSRLS, so the orchestrator readers of these views
-- (scripts/apply_held_commitments.py [deleted 2026-09-05 PR #89], the SLA watchdog) are unaffected.

BEGIN;

ALTER VIEW public.agent_observed_activity SET (security_invoker = on);
ALTER VIEW public.held_commitments_due    SET (security_invoker = on);

REVOKE ALL ON public.agent_observed_activity FROM anon, authenticated;
REVOKE ALL ON public.held_commitments_due    FROM anon, authenticated;

COMMIT;
