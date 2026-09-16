-- 064_project_governance_cai_gate.sql
-- ledger: silo=tscuymavysscrvoberrr
-- assert: no_execute anon public.project_for_agent(text)
-- assert: no_execute anon public.project_for_repos(text[])
-- assert: no_execute anon public.project_for_repo(text)
-- assert: no_execute anon public.agent_messages_cai_gate()
-- assert: no_execute anon public.strategic_decisions_cai_gate()
-- assert: no_execute authenticated public.agent_messages_cai_gate()
-- assert: no_execute authenticated public.strategic_decisions_cai_gate()
-- (orchestrator substrate)
--
-- REVISED (2026-09-16, orch-console gate #40738 on PR#114 head a3c04e21): 3
-- required fixes -- B1 the strategic_decisions gate over-blocked (it resolved
-- repos_affected to a SINGLE highest-priority project and blocked if THAT one
-- was cai-off, even when only one of several listed repos belonged to a
-- gated project; real data showed 15 legitimate cai-decided rows in 7 days
-- would have been wrongly refused). Now resolves EACH repos_affected element
-- independently (project_for_repo, singular) and blocks only if EVERY
-- element resolves to a cai_enabled=false project -- an element that is
-- ungoverned (NULL) or belongs to a cai-on project lets the row through.
-- B2 project_governance_families had no unique constraint, so ON CONFLICT
-- DO NOTHING silently matched nothing and every re-run duplicated the 15
-- seed rows -- added UNIQUE (match_type, pattern), named explicitly in both
-- ON CONFLICT clauses. B3 the two gate trigger functions are SECURITY
-- DEFINER but had no REVOKE (unlike migration 063's audit trigger) -- locked
-- down to match, with matching asserts above.
--
-- BUILD ORDER (P1, Musa op#20702/20704/20706/20708; orch-console bus #40726 ruling
-- on Stage B). Makes the project_governance.cai_enabled toggle (Stage A, migration
-- 063) REAL at the ONE place every path already converges: the substrate DB itself.
--
-- WHY A DB TRIGGER, NOT A PYTHON GATE: cai_review_request.py (the module the
-- original build order named) is LEGACY -- its only caller (legacy/wingmen_orch.py)
-- was itself retired to legacy/ earlier (op#19103 PR#91). The LIVE mechanism by
-- which anything reaches cai today is every agent inserting directly (its own
-- psycopg) into agent_messages (to_agent='cai') or strategic_decisions
-- (decided_by='cai') -- there is no shared Python entry point to gate. A Python
-- wrapper would be convention again, exactly the gap this build order exists to
-- close. Enforcing at the DB means EVERY insert path is covered, present and
-- future, with no code to remember to call.
--
-- TOGGLE SEMANTICS (unchanged from Stage A, restated per orch-console ruling):
-- cai_enabled=false means cai's precedent does NOT bind that project and cai is
-- NOT consulted BY that project's bodies for review/decision requests. It does
-- NOT mute cai's own voice (cai may still post updates/agreed/decision rows
-- anywhere -- those message_types are NOT gated) and it does NOT touch
-- require_verified_authorization.py or anything money/irreversible (Musa-only,
-- unconditionally, until a separate Stage D).
--
-- ---------------------------------------------------------------------------
-- PART 1 -- family resolution (data-driven: adding a project is a data row,
-- not a code change).
-- ---------------------------------------------------------------------------

ALTER TABLE public.agent_messages ADD COLUMN IF NOT EXISTS project TEXT;
COMMENT ON COLUMN public.agent_messages.project IS
  'Explicit project tag (op#20702 Stage B). When set, wins over family-inference '
  'from from_agent (project_for_agent). NULL = infer from from_agent, or '
  '(if that is also NULL) ungoverned by any project toggle.';

CREATE TABLE IF NOT EXISTS public.project_governance_families (
  id          SERIAL      PRIMARY KEY,
  match_type  TEXT        NOT NULL CHECK (match_type IN ('agent_prefix', 'repo_pattern')),
  pattern     TEXT        NOT NULL,   -- agent_prefix: a POSIX regex tested via `~`;
                                       -- repo_pattern: an ILIKE pattern tested against
                                       -- each element of strategic_decisions.repos_affected
  project     TEXT        NOT NULL,
  priority    INTEGER     NOT NULL DEFAULT 100,  -- lower = checked first; first match wins
  -- B2 (orch-console gate #40738): without this, ON CONFLICT DO NOTHING below has no
  -- constraint to match against, so it is a silent no-op and every re-run duplicates
  -- the seed rows.
  UNIQUE (match_type, pattern)
);

COMMENT ON TABLE public.project_governance_families IS
  'Data-driven family->project resolution for the cai gate (op#20702 Stage B). Two '
  'independent matchers share this table by match_type: agent_prefix (from_agent, '
  'regex via ~) and repo_pattern (strategic_decisions.repos_affected elements, ILIKE). '
  'Add a family by INSERTing a row, not by changing code.';

CREATE INDEX IF NOT EXISTS project_governance_families_lookup_idx
  ON public.project_governance_families (match_type, priority);

-- agent_prefix seed, exactly per orch-console ruling #40726: fleet/substrate bodies
-- (orch-console, cc-orchestrator, cai, cc-fleet-health, cc-quality, substrate) are
-- deliberately absent -> resolve to NULL -> ungoverned, cai stays reachable for them.
INSERT INTO public.project_governance_families (match_type, pattern, project, priority) VALUES
  ('agent_prefix', '^cc-irsyad',     'irsyad',     10),
  ('agent_prefix', '^cc-cosem',      'cosem',      10),
  ('agent_prefix', '^cc-ihsanos',    'ihsanos',    10),
  ('agent_prefix', '^cc-storefront', 'ihsanos',    10),
  ('agent_prefix', '^cc-scholar',    'scholar',    10),
  ('agent_prefix', '^cc-shipforge',  'shipforge',  10),
  ('agent_prefix', '^cc-finance',    'finance',    10)
ON CONFLICT (match_type, pattern) DO NOTHING;

-- repo_pattern seed: irsyad checked BEFORE ihsanos (priority 10 < 20) so
-- 'ihsanos-irsyad' resolves to 'irsyad' per the ruling's explicit example, not
-- 'ihsanos'. Two ADDITIONS beyond the ruling's literal list, found by inspecting
-- real repos_affected values before writing this (30-day sample): 'adcda' (cosem's
-- client, per fleet doctrine "cosem & ADCDA") and 'goumlynecruxrlmzlntp' (the raw
-- irsyad silo project ref, which appears bare in some rows) -- flagged in the
-- dry-run report for an explicit accept/reject, not silently assumed.
INSERT INTO public.project_governance_families (match_type, pattern, project, priority) VALUES
  ('repo_pattern', '%irsyad%',              'irsyad',    10),
  ('repo_pattern', '%goumlyne%',            'irsyad',    10),  -- ADDITION: raw silo ref alias
  ('repo_pattern', '%ihsanos%',             'ihsanos',   20),
  ('repo_pattern', '%cosem%',               'cosem',     10),
  ('repo_pattern', '%adcda%',               'cosem',     10),  -- ADDITION: cosem's client name alone
  ('repo_pattern', '%scholar%',             'scholar',   10),
  ('repo_pattern', '%shipforge%',           'shipforge', 10),
  ('repo_pattern', '%finance%',             'finance',   10)
ON CONFLICT (match_type, pattern) DO NOTHING;

CREATE OR REPLACE FUNCTION public.project_for_agent(agent TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
SET search_path = public
AS $fn$
  SELECT f.project
    FROM public.project_governance_families f
   WHERE f.match_type = 'agent_prefix'
     AND agent ~ f.pattern
   ORDER BY f.priority, f.id
   LIMIT 1;
$fn$;

COMMENT ON FUNCTION public.project_for_agent(TEXT) IS
  'Resolve an agent_messages.from_agent value to its governed project by regex '
  'prefix (project_governance_families, match_type=agent_prefix). NULL = no '
  'matching family -- a fleet/substrate body, ungoverned by any project toggle.';

CREATE OR REPLACE FUNCTION public.project_for_repos(repos TEXT[])
RETURNS TEXT
LANGUAGE sql
STABLE
SET search_path = public
AS $fn$
  SELECT f.project
    FROM public.project_governance_families f
   WHERE f.match_type = 'repo_pattern'
     AND EXISTS (SELECT 1 FROM unnest(repos) r WHERE r ILIKE f.pattern)
   ORDER BY f.priority, f.id
   LIMIT 1;
$fn$;

COMMENT ON FUNCTION public.project_for_repos(TEXT[]) IS
  'Resolve a strategic_decisions.repos_affected array to ONE governed project (the '
  'single highest-priority family matched by ANY element). NULL = no matching family. '
  'Display/summary use only -- the cai gate (PART 3 below) resolves per-element via '
  'project_for_repo, since a decision touching several repos must not be blocked just '
  'because ONE of them happens to belong to a cai-off project.';

CREATE OR REPLACE FUNCTION public.project_for_repo(repo TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
SET search_path = public
AS $fn$
  SELECT f.project
    FROM public.project_governance_families f
   WHERE f.match_type = 'repo_pattern'
     AND repo ILIKE f.pattern
   ORDER BY f.priority, f.id
   LIMIT 1;
$fn$;

COMMENT ON FUNCTION public.project_for_repo(TEXT) IS
  'B1 (orch-console gate #40738): resolve a SINGLE repo string to its governed project '
  '(project_governance_families, match_type=repo_pattern). Used by the strategic_decisions '
  'cai gate to resolve each repos_affected element independently, so a decision listing '
  'several repos is judged per-element, not by whichever one matches first. NULL = no '
  'matching family -- that element is ungoverned.';

REVOKE ALL ON FUNCTION public.project_for_agent(TEXT) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.project_for_repos(TEXT[]) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.project_for_repo(TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.project_for_agent(TEXT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.project_for_repos(TEXT[]) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.project_for_repo(TEXT) TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- PART 2 -- the agent_messages gate. Only the 4 review-seeking message_types are
-- blocked (review_request/question/blocker/challenge); update/agreed/decision
-- rows to cai are informational and never blocked -- cai may still be told.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.agent_messages_cai_gate()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  p TEXT;
BEGIN
  p := COALESCE(NEW.project, public.project_for_agent(NEW.from_agent));
  IF p IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.project_governance g
     WHERE g.project = p AND g.cai_enabled = false
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = 'check_violation',
      MESSAGE = format(
        'cai is OFF for project %s (op#20708): route this to orch-console '
        '(operator-domain), not cai', p
      );
  END IF;
  RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.agent_messages_cai_gate() IS
  'op#20702 Stage B: refuses a review_request/question/blocker/challenge row to '
  'cai (to_agent=''cai'') when the sending project has cai_enabled=false. '
  'update/agreed/decision rows are never gated (informational; cai may still be '
  'told). Money/irreversible authorization is untouched -- see migration header.';

DROP TRIGGER IF EXISTS trg_agent_messages_cai_gate ON public.agent_messages;
CREATE TRIGGER trg_agent_messages_cai_gate
  BEFORE INSERT ON public.agent_messages
  FOR EACH ROW
  WHEN (NEW.to_agent = 'cai' AND NEW.message_type IN ('review_request', 'question', 'blocker', 'challenge'))
  EXECUTE FUNCTION public.agent_messages_cai_gate();

-- B3 (orch-console gate #40738): this trigger function is SECURITY DEFINER (owned by
-- postgres) so it can always evaluate the gate regardless of the inserting role's own
-- grants; lock its own EXECUTE down to exactly who needs to invoke it (the trigger
-- mechanism, which does not go through GRANT EXECUTE checks for trigger firing -- this
-- REVOKE only prevents a role from calling it directly as an ordinary function).
REVOKE ALL ON FUNCTION public.agent_messages_cai_gate() FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- PART 3 -- the strategic_decisions gate: cai cannot BIND (decided_by='cai') a
-- decision whose repos_affected are ALL governed by cai-off projects.
-- Unresolvable / non-cai-decided rows pass untouched.
--
-- B1 (orch-console gate #40738, REVISED from the original single-resolve
-- version): resolving the WHOLE repos_affected array to one highest-priority
-- project and blocking if THAT one was cai-off over-blocked -- real data,
-- last 7 days, showed 15 legitimate cai-decided rows would have been wrongly
-- refused, including CAI-RESP-1422..1425 (repos ['irsyad','ihsanos',
-- 'orchestrator']), which are SUBSTRATE controls that merely list irsyad as
-- an affected repo; cai governs the substrate and MUST be able to write
-- those. Now each element is resolved INDEPENDENTLY (project_for_repo) and
-- the row is blocked only if EVERY element resolves to a cai_enabled=false
-- project -- an element that is ungoverned (no matching family) or belongs
-- to a cai-on project lets the row through. Under this rule the same 7 days
-- block exactly 5 rows (CAI-RESP-1432/1431/1430 cosem-adcda-only post-
-- op#20139, 1414/1409 irsyad) -- precisely the drift the toggle exists to
-- stop.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.strategic_decisions_cai_gate()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  r               TEXT;
  p               TEXT;
  any_repo        BOOLEAN := false;
  all_gated_off   BOOLEAN := true;
  gated_projects  TEXT[]  := '{}';
BEGIN
  IF NEW.decided_by = 'cai' AND NEW.repos_affected IS NOT NULL THEN
    FOREACH r IN ARRAY NEW.repos_affected LOOP
      any_repo := true;
      p := public.project_for_repo(r);
      IF p IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.project_governance g
         WHERE g.project = p AND g.cai_enabled = false
      ) THEN
        gated_projects := array_append(gated_projects, p);
      ELSE
        -- this element is ungoverned, or belongs to a cai-on project --
        -- that alone is enough for the whole row to pass.
        all_gated_off := false;
      END IF;
    END LOOP;

    IF any_repo AND all_gated_off THEN
      RAISE EXCEPTION USING
        ERRCODE = 'check_violation',
        MESSAGE = format(
          'cai is OFF for every project this decision touches (%s) (op#20708): '
          'cai cannot bind this decision -- route to Musa / the project operator '
          'instead', array_to_string(gated_projects, ', ')
        );
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.strategic_decisions_cai_gate() IS
  'op#20702 Stage B: refuses a strategic_decisions INSERT with decided_by=''cai'' '
  'ONLY when EVERY element of repos_affected resolves to a project with '
  'cai_enabled=false (per-element via project_for_repo; an element that resolves to '
  'NULL/ungoverned or a cai-on project lets the row PASS; empty/NULL arrays pass). '
  'Rows decided by anyone else pass untouched.';

DROP TRIGGER IF EXISTS trg_strategic_decisions_cai_gate ON public.strategic_decisions;
CREATE TRIGGER trg_strategic_decisions_cai_gate
  BEFORE INSERT ON public.strategic_decisions
  FOR EACH ROW
  EXECUTE FUNCTION public.strategic_decisions_cai_gate();

-- B3 (orch-console gate #40738): same reasoning as agent_messages_cai_gate above.
REVOKE ALL ON FUNCTION public.strategic_decisions_cai_gate() FROM PUBLIC, anon, authenticated;
