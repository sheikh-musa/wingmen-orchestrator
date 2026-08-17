-- 043_agents_single_owner_repo_scope_guard.sql
--
-- WHY (op#13060 pool-move incident, 2026-08-14; operator/console follow-up bus 21626):
-- A latent FLEET-DOWN was surfaced when cc-fleet-health re-tokened a lane: the
-- irsyad family had OVER-CLAIMED repos in agents.repo_scope — cc-irsyad (base),
-- cc-irsyad-coord, and cc-irsyad-receipt all claimed ['ihsanos-irsyad','irsyad'],
-- plus cc-irsyad-student claimed ['ihsanos-irsyad'] (all 3 sub-identities seeded
-- today by direct INSERTs that bypassed the Python registration scripts). This is
-- the SAME class as op#11326 (register_quality's '*' colliding with cc-reviewer's).
--
-- auto_agent_id.load_family_map (scripts/lib/auto_agent_id.py:64-74) RAISES if two
-- cc-% agents claim the same canonical repo — so EVERY normal (pwd-resolved) lane
-- launch dies while the conflict exists (switch_lane_token / reset_lane /
-- auto-recycle / crash-recovery all broken). The failure is LATENT: invisible while
-- everything runs, FATAL on the next relaunch. The console asked for the durable
-- fix: FAIL LOUD AT WRITE TIME (registration), not silently at launch.
--
-- THIS MIGRATION: a BEFORE INSERT OR UPDATE OF repo_scope trigger on public.agents
-- that enforces the single-owner invariant with load_family_map's EXACT semantics:
--   * only cc-% agents are guarded (mirrors `WHERE id LIKE 'cc-%'`);
--   * canonicalize each repo by stripping a leading 'wingmen-' (mirrors the map);
--   * a canonical repo already claimed by a DIFFERENT cc-% agent -> RAISE loud.
-- `UPDATE OF repo_scope` (not bare UPDATE) means routine writes (heartbeat lives in
-- agent_status, not here, but also current_task/status/display_name updates) do NOT
-- fire the guard — only writes that TOUCH repo_scope are validated. Self (NEW.id) is
-- excluded so re-saving an agent's own scope is always allowed. '*' is handled as a
-- normal repo string, so the single-'*' invariant ([[agents-repo-scope-single-star-invariant]])
-- is covered by the same guard.
--
-- SCOPE: bus/coordination silo tscuymavysscrvoberrr. Apply via cai §6.6 guarded
-- path (orch-console executor, never db push). Runs as owner (postgres). Idempotent
-- (CREATE OR REPLACE + DROP TRIGGER IF EXISTS).
--
-- SEQUENCING (IMPORTANT): apply this ONLY AFTER the existing irsyad over-claim is
-- CLEARED (the coord/cai-approved UPDATE: cc-irsyad-coord/-receipt/-student
-- repo_scope -> '{}', leaving cc-irsyad sole owner). The guard is safe to install
-- before the clear too (it only fires on repo_scope writes, and the clearing UPDATE
-- sets '{}' which trivially passes) — but installing AFTER the clear means a clean
-- table with zero pre-existing violations, which is the intended steady state.

BEGIN;

CREATE OR REPLACE FUNCTION public.agents_single_owner_repo_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_repo   text;
    v_canon  text;
    v_other  text;
BEGIN
    -- Only cc-% agents participate in the family map (auto_agent_id.load_family_map).
    IF NEW.id NOT LIKE 'cc-%' OR NEW.repo_scope IS NULL THEN
        RETURN NEW;
    END IF;

    FOREACH v_repo IN ARRAY NEW.repo_scope LOOP
        -- Canonicalize: strip a leading 'wingmen-' (mirrors load_family_map).
        v_canon := CASE WHEN v_repo LIKE 'wingmen-%'
                        THEN substring(v_repo FROM 9)
                        ELSE v_repo END;

        -- Is this canonical repo already claimed by a DIFFERENT cc-% agent?
        SELECT a.id
          INTO v_other
          FROM public.agents a
         WHERE a.id LIKE 'cc-%'
           AND a.id <> NEW.id
           AND EXISTS (
                 SELECT 1
                   FROM unnest(a.repo_scope) AS r(repo)
                  WHERE (CASE WHEN r.repo LIKE 'wingmen-%'
                              THEN substring(r.repo FROM 9)
                              ELSE r.repo END) = v_canon
               )
         LIMIT 1;

        IF v_other IS NOT NULL THEN
            RAISE EXCEPTION
              'agents.repo_scope single-owner violation: repo % (canonical %) already claimed by % — cannot also assign to %. Narrow one claim before writing (mig-043 guard; this is the op#11326 duplicate-repo_scope pattern that otherwise fails LATENTLY at lane launch).',
              v_repo, v_canon, v_other, NEW.id;
        END IF;
    END LOOP;

    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_agents_single_owner_repo_scope ON public.agents;
CREATE TRIGGER trg_agents_single_owner_repo_scope
    BEFORE INSERT OR UPDATE OF repo_scope ON public.agents
    FOR EACH ROW
    EXECUTE FUNCTION public.agents_single_owner_repo_scope();

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6)
-- ===========================================================================
-- Verified by cc-fleet-health (rolled-back psycopg txn, migrations/043_verify.py):
--   * clean INSERT of a fresh-repo cc-% agent            -> PASS
--   * INSERT of a cc-% agent claiming an already-owned   -> RAISES (single-owner)
--     repo (e.g. 'ihsanos-irsyad', or a repo a prior test row in the txn owns)
--   * 'wingmen-foo' vs 'foo' treated as the SAME canon    -> RAISES (canonicalized)
--   * narrowing a duplicate row's repo_scope to '{}'      -> PASS (clears conflict)
--   * UPDATE of a NON-repo_scope column on a still-        -> PASS (UPDATE OF repo_scope
--     conflicting row (e.g. current_task)                    scopes the trigger)
--   * INSERT of a NON cc-% id ('human-x') with a dup repo -> PASS (guard is cc-% only)
--   * self re-save (same id, same scope)                  -> PASS (NEW.id excluded)
-- SEQUENCING: apply AFTER the irsyad over-claim clear (coord/receipt/student -> '{}').
-- REVERSIBILITY: DROP TRIGGER trg_agents_single_owner_repo_scope ON public.agents;
--                DROP FUNCTION public.agents_single_owner_repo_scope();
-- LEDGER: repo=orchestrator, migration_name=043_agents_single_owner_repo_scope_guard.sql
