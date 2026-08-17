-- 052_escalator_anon_execute_revoke.sql
-- Close an anon-reachable escalator, durably. Found by cc-quality; the function is mine.
--
-- THE FINDING (cc-quality, 2026-08-17, proven over the wire — not inferred from a catalog):
--     anon -> /rest/v1/rpc/escalate_full_tier_without_auditor  ->  HTTP 200, EXECUTED
--     anon -> /rest/v1/rpc/escalate_stale_decision_audits      ->  HTTP 401, 42501
--   Same schema, same night, same author. The older one was revoked; this one shipped
--   carrying PostgreSQL's DEFAULT PUBLIC EXECUTE, and `public` is PostgREST-exposed.
--
-- WHY IT MATTERS, and it is not "an unauthenticated user can send a message early":
--   The function is deliberately ONE-SHOT per decision via `decision_tier_escalations` —
--   which is the right design. So an anonymous caller passing p_grace_hours=0 does not merely
--   fire early: it BURNS THE DEDUP for that decision, and the escalation can then never fire
--   again. That silently disables the §2 sink. **The one-shot guard is what turns a nuisance
--   into a durable hole** — the correctness feature is the exploit.
--
-- THIS IS THE `purge_wc_ingest_pii` CLASS, IN A FUNCTION I WROTE. I spent the hour before this
--   auditing 12 SECURITY DEFINER functions on the money tenant for exactly this defect, and
--   shipped it myself on the substrate in the same night. The audit was pointed outward; the
--   habit that produces the defect was not.
--
-- CONTAINED AT RUNTIME FIRST (05:15Z), then codified here, because a live anon-reachable
--   hazard is a contain-now (cai's CAI-1023 precedent on purge_wc_ingest_pii: verified zero
--   callers, tightening, reversible -> revoke immediately, disclose immediately).
--
-- ⚠ THIS FILE EXISTS BECAUSE A RUNTIME REVOKE RE-OPENS ON REDEPLOY. Default PUBLIC EXECUTE
--   re-applies on every CREATE OR REPLACE, so a hand-run REVOKE is a FALSE FIX that greens now
--   and silently regresses later. The durable half of `purge_wc_ingest_pii` is STILL OWED for
--   exactly this reason; this one is not going to join it.
--
-- PRE-REVOKE CHECKS RUN AT SOURCE (the rule I had to learn the hard way earlier tonight —
-- app-caller greps are necessary and INSUFFICIENT, because the database is itself a caller):
--   * RLS policies referencing it ......... 0   (a policy-referenced grant is LOAD-BEARING;
--                                                revoking it errors the query, not narrows it)
--   * called inside other functions ....... 0
--   * code callers in this repo ........... 0   (neither this nor its sibling; both are
--                                                invoked out-of-repo)
--   * sibling precedent ................... escalate_stale_decision_audits already runs on
--                                            service_role+postgres ONLY and demonstrably fires
--                                            (it paged cai for real on 2026-08-16), which
--                                            PROVES the target shape works for this class.
--   * full proacl read .................... anon/authenticated held EXPLICIT grants, not just
--                                            the PUBLIC blanket, so revoking PUBLIC alone would
--                                            NOT have closed it. All three had to go.
--
-- RESULT: proacl now byte-identical to the sibling — {postgres=X/postgres,service_role=X/postgres}.

BEGIN;

REVOKE EXECUTE ON FUNCTION public.escalate_full_tier_without_auditor(numeric)
    FROM PUBLIC, anon, authenticated;

-- Post-condition asserts MY OWN EFFECT, not world-state: on a live substrate any "nothing else
-- changed" assertion is tripped by other bodies simply working. Fails SAFE — a spurious
-- rollback, never a false green.
DO $$
DECLARE
    v_anon boolean; v_auth boolean; v_pub boolean; v_svc boolean;
BEGIN
    SELECT has_function_privilege('anon', p.oid, 'EXECUTE'),
           has_function_privilege('authenticated', p.oid, 'EXECUTE'),
           has_function_privilege('public', p.oid, 'EXECUTE'),
           has_function_privilege('service_role', p.oid, 'EXECUTE')
      INTO v_anon, v_auth, v_pub, v_svc
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'escalate_full_tier_without_auditor';

    IF v_anon OR v_auth OR v_pub THEN
        RAISE EXCEPTION 'escalator still web-reachable after revoke (anon=% auth=% public=%)',
            v_anon, v_auth, v_pub;
    END IF;

    -- The negative control. A revoke that also stripped service_role would not be a fix, it
    -- would be an outage: service_role is what actually invokes the escalator.
    IF NOT v_svc THEN
        RAISE EXCEPTION 'service_role LOST EXECUTE — this would silence the escalator entirely';
    END IF;
END $$;

COMMIT;
