-- 003_cc_reviewer.sql — stand up cc-reviewer (on-demand independent reviewer) per CAI-RESP-257.
-- Substrate writes ONLY; no code grant. Reviewed dry-run -> cai -> apply (decision-962).
-- Posted to cai on thread ef567cb4 for review before --apply.

-- 1) Base agent identity. On-demand, repo-agnostic (scope '*'). Idle until spawned.
INSERT INTO agents (id, display_name, repo_scope, status)
VALUES ('cc-reviewer', 'CC Reviewer', ARRAY['*']::text[], 'idle')
ON CONFLICT (id) DO NOTHING;

-- 2) fleet_lanes: declare the reviewer as an ON-DEMAND lane.
--    Two operator-authorized constraint evolutions (anti-drift preserved —
--    liveness is still derived on read, never stored):
--    (a) desired_state gains 'on-demand' — distinguishes "spawned per review"
--        from 'down' (= operator wants it off). lanes.sh ignores non-'up' lanes,
--        so the reviewer is never auto-booted; the spawner handles it.
--    (b) worktree_path becomes nullable — an on-demand reviewer is tree-agnostic;
--        the spawner passes the target repo/diff at spawn time.
ALTER TABLE fleet_lanes DROP CONSTRAINT IF EXISTS fleet_lanes_desired_state_check;
ALTER TABLE fleet_lanes ADD CONSTRAINT fleet_lanes_desired_state_check
  CHECK (desired_state IN ('up', 'down', 'paused', 'on-demand'));
ALTER TABLE fleet_lanes ALTER COLUMN worktree_path DROP NOT NULL;

INSERT INTO fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES ('reviewer', NULL, NULL, 'claude-opus-4-8', 'launch_dangerous_cc.sh', 'cc-reviewer', 'on-demand',
        'CAI-RESP-257 on-demand independent reviewer. Fresh-spawn per review (sub-tag cc-reviewer-N), '
        || 'never inherits builder/orchestrator context, read-only, verdicts artifact-cited + advisory to cai '
        || '(reviews, never rules; no execution authority). Tree-agnostic: spawner passes target repo/diff. '
        || 'Not lanes.sh-booted.')
ON CONFLICT (lane) DO NOTHING;

-- 3) review_dimensions registry — mandatory lenses the reviewer applies to
--    money-touching and PII/auth diffs (finance + security stay DIMENSIONS, not
--    standing agents, per CAI-RESP-257; promote to agents only on bottleneck evidence).
CREATE TABLE IF NOT EXISTS review_dimensions (
    name         text PRIMARY KEY,
    description  text NOT NULL,
    applies_when text NOT NULL,
    mandatory    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE review_dimensions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS review_dimensions_service ON review_dimensions;
CREATE POLICY review_dimensions_service ON review_dimensions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

INSERT INTO review_dimensions (name, description, applies_when) VALUES
  ('finance',
   'Money-touching correctness: payment/GIRO/Stripe/OCBC parsing, unsuccessful-txn exclusion, totals & reconciliation invariants, no double-count.',
   'diff touches money flows, totals, reconciliation, or payment integrations'),
  ('security',
   'PII / auth / secrets: donor-data exposure, PDPA, auth gates, signature/credential handling, RLS, secret leakage.',
   'diff touches PII, auth, secrets, RLS, or credential/signature handling')
ON CONFLICT (name) DO NOTHING;
