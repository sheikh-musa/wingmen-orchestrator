-- 046_fleet_proposals.sql — the ideas-up ledger (operator op#13332).
--
-- WHY: on 2026-08-15 the operator asked "why didn't you suggest this then" after HE saw a
-- better architecture than the one the fleet was busy optimising. The diagnosis in
-- docs/self-improvement-loop-spec.md is structural: every trigger an agent responds to is a
-- task, a gate, an alert or a review request — all four reactive to work that already exists.
-- Nothing ever asks an agent what should be DIFFERENT, so proposals only surface when the
-- operator's frustration creates the opening. That makes him the fleet's proposal mechanism,
-- which is precisely what he is asking to stop being.
--
-- This table makes "here is a better shape" a first-class output with a place to go.
--
-- NOT a duplicate of bug_reports: that is the CLIENT bug-intake pipeline (reporter_email,
-- client_id, screenshot_url, auto-fix tiers). This is internal, agent-authored, and about
-- CHANGE rather than breakage.
--
-- Apply with scripts/apply_mig046_fleet_proposals.py (direct psycopg).
-- NEVER `supabase db push` against production (decision 962).

CREATE TABLE IF NOT EXISTS public.fleet_proposals (
  id            bigserial PRIMARY KEY,
  from_agent    text        NOT NULL,
  problem       text        NOT NULL,
  -- REQUIRED, and the point of the whole table: a row without a suggested change is a bug
  -- report, and we already have channels for those. The CHECK is the enforcement — an agent
  -- cannot file a complaint here and call it a proposal.
  proposal      text        NOT NULL CHECK (length(btrim(proposal)) > 0),
  -- How the author KNOWS (pane / log / DB output). Verified at source, not inferred — the
  -- discipline that caught five real errors on 2026-08-15.
  evidence      text        NOT NULL DEFAULT '',
  -- Free-text class, clustered at triage. This is the field that makes a REPEAT visible;
  -- without it every failure looks new and gets re-discovered instead of remembered.
  failure_class text        NOT NULL DEFAULT 'unclassified',
  -- Who paid for finding this. 'operator-caught' is the expensive one and drives the metric
  -- the whole loop is judged on (see fleet_proposal_metrics_v).
  cost_signal   text        NOT NULL DEFAULT 'agent-caught'
                  CHECK (cost_signal IN ('operator-caught', 'agent-caught', 'near-miss')),
  status        text        NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new', 'triaged', 'accepted', 'rejected', 'shipped')),
  -- Accepted proposals must land as a gate/test/guard, never as a norm an agent promises to
  -- remember: a norm does not survive a context reset (feedback_enforce_process_in_code_not_promises).
  -- Recorded here so "shipped" means something checkable.
  landed_as     text,
  decided_by    text,
  decided_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  is_test       boolean     NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS fleet_proposals_open_idx
  ON public.fleet_proposals (status, created_at DESC) WHERE NOT is_test;
CREATE INDEX IF NOT EXISTS fleet_proposals_class_idx
  ON public.fleet_proposals (failure_class) WHERE NOT is_test;

COMMENT ON TABLE public.fleet_proposals IS
  'Ideas-up ledger (op#13332). Any agent files "what should be different" the moment it '
  'notices. Triage is cai + orch-console; the operator sees the loop''s OUTPUT (a short '
  'digest), never its queue — handing him the queue would just be a new thing for him to catch.';

-- The two numbers the loop is judged on. Deliberately a view: a stored metric is a metric
-- that goes stale, which is the same class of bug as the snapshot board fixed the same day.
--
-- operator_caught_pct is the north star: the share of defects the OPERATOR found first.
-- It cannot be gamed by filing more — the denominator grows too. Only finding things
-- earlier moves it, and 0 means he has stopped being the fleet's error detector.
CREATE OR REPLACE VIEW public.fleet_proposal_metrics_v AS
WITH w AS (
  SELECT * FROM public.fleet_proposals
  WHERE NOT is_test AND created_at > now() - interval '30 days'
)
SELECT
  (SELECT count(*) FROM w)                                              AS filed_30d,
  (SELECT count(*) FROM w WHERE cost_signal = 'operator-caught')        AS operator_caught_30d,
  CASE WHEN (SELECT count(*) FROM w) = 0 THEN NULL
       ELSE round(100.0 * (SELECT count(*) FROM w WHERE cost_signal = 'operator-caught')
                        / (SELECT count(*) FROM w), 1)
  END                                                                   AS operator_caught_pct,
  -- A class seen more than once in the window is a failure we did not actually learn from.
  (SELECT count(*) FROM (
      SELECT failure_class FROM w WHERE failure_class <> 'unclassified'
      GROUP BY failure_class HAVING count(*) > 1) r)                    AS repeat_classes_30d,
  (SELECT count(*) FROM w WHERE status = 'shipped')                     AS shipped_30d,
  (SELECT count(*) FROM w WHERE status IN ('new', 'triaged'))           AS open_now;

COMMENT ON VIEW public.fleet_proposal_metrics_v IS
  'operator_caught_pct is the loop''s north star — the share of defects the operator found '
  'first, target 0. If it has not moved in a month the loop was the wrong idea and should be '
  'killed rather than tuned.';
