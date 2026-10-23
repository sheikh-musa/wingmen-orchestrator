-- 116_provider_policy_lane_override.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_table_privilege anon public.provider_policy_lane_override SELECT
-- assert: no_table_privilege authenticated public.provider_policy_lane_override SELECT
-- assert: no_table_privilege anon public.provider_policy_lane_override UPDATE
-- assert: no_table_privilege authenticated public.provider_policy_lane_override UPDATE
-- assert: no_table_privilege anon public.provider_policy_lane_override INSERT
-- assert: no_table_privilege authenticated public.provider_policy_lane_override INSERT
--
-- provider_policy_lane_override — EXACT-lane (not client-level) provider
-- restriction, consulted BEFORE provider_policy (orch-console #74584/#74588,
-- bus #74586 spec, reports/provider-policy-flip-gate-spec-20261023.md).
--
-- WHY A SEPARATE TABLE: provider_policy is client-key-level (one row covers
-- EVERY lane under that client). Some lanes need to be STRICTER than their
-- client's default (e.g. cosem-adcda-photos must stay Anthropic-only even
-- though plain cosem-adcda is GLM text-only cleared — the image/minors-
-- residency bar, CAI-1340, applies to that one specific lane, not the whole
-- client). This table is that single additional, narrower layer; it was
-- previously a hardcoded HARD_EXCLUDE_LANES constant in
-- scripts/glm_peak_pause_resume.py, which drifted from provider_policy with
-- no mechanism forcing them to agree (the mamadah incident, one level up).
--
-- INVARIANT (enforced in Python, scripts/lib/provider_policy.py, NOT a DB
-- CHECK -- it depends on joining against provider_policy's current row,
-- which this table cannot see at INSERT time): an override may only NARROW
-- the client-level resolution, never widen it. A lane_override row whose
-- allowed_providers is NOT a subset of its resolved client_key's
-- allowed_providers is a misconfiguration the resolver fails CLOSED on
-- (raises, never silently honors the wider value) every time it is read,
-- not just at write time -- so a future hand-edit that violates this is
-- still caught.
--
-- SAFETY (fail-closed) on three sides, same shape as provider_policy (104):
--   * resolver: an unmapped lane is NOT in this table -> falls through to
--     provider_policy's own client-level resolution unchanged.
--   * CHECKs: same 'anthropic always present' / 'non-anthropic requires
--     text_only' shape as provider_policy, for internal consistency.
--   * RLS/REVOKE: only service_role writes; console reads; anon/authenticated
--     locked out. Same control-table lockdown as 104.
--
-- Seed rows migrate scripts/glm_peak_pause_resume.py's HARD_EXCLUDE_LANES
-- EXACT-name set (glm-rollout-plan-20261013 line 35): cosem-adcda-photos +
-- cc-fleet-health. The set's PREFIX half (irsyad/hub/console/orch-console)
-- is deliberately NOT migrated here as rows: irsyad already resolves via its
-- own anthropic-only provider_policy row (client-level coverage, no override
-- needed), and hub/console/orch-console have no provider_policy row at all
-- so they already fail CLOSED to Anthropic-only by the resolver's own
-- unknown-client-key default -- an explicit override row for them would be
-- redundant, not missing coverage. (The old constant stays in code for one
-- deploy cycle, run in PARALLEL with this table, warn-on-divergence, per
-- orch-console's Q5 decision -- not deleted in this migration.)

CREATE TABLE IF NOT EXISTS provider_policy_lane_override (
  lane              text PRIMARY KEY,
  allowed_providers text[]      NOT NULL DEFAULT ARRAY['anthropic']::text[],
  text_only         boolean     NOT NULL DEFAULT false,
  cleared_by        text,
  evidence          text,
  created_by        text        NOT NULL DEFAULT 'cc-fleet-health',
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT provider_policy_lane_override_anthropic_always
    CHECK ('anthropic' = ANY(allowed_providers)),
  CONSTRAINT provider_policy_lane_override_nonanthropic_requires_text_only
    CHECK (allowed_providers <@ ARRAY['anthropic']::text[] OR text_only = true)
);

COMMENT ON TABLE provider_policy_lane_override IS
  'Exact-lane provider restriction, consulted BEFORE provider_policy''s client-level row. May only NARROW the client-level resolution (enforced in scripts/lib/provider_policy.py on every read, not just at write time). Control table: service_role-only writes (RLS), console read-only.';

-- RLS + REVOKE (control-table lockdown, same shape as 104). Re-run-safe.
ALTER TABLE public.provider_policy_lane_override ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS provider_policy_lane_override_service_only ON public.provider_policy_lane_override;
CREATE POLICY provider_policy_lane_override_service_only ON public.provider_policy_lane_override
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS provider_policy_lane_override_console_ro ON public.provider_policy_lane_override;
CREATE POLICY provider_policy_lane_override_console_ro ON public.provider_policy_lane_override
  FOR SELECT TO console_readonly USING (true);
REVOKE ALL ON public.provider_policy_lane_override FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.provider_policy_lane_override TO console_readonly;
GRANT ALL ON public.provider_policy_lane_override TO service_role;

INSERT INTO provider_policy_lane_override (lane, allowed_providers, text_only, cleared_by, evidence) VALUES
  ('cosem-adcda-photos', ARRAY['anthropic']::text[], false, 'glm-rollout-plan-20261013 line 35',
     'HARD-EXCLUDED from GLM by the image/minors-residency bar (orch-console #67675, confirming this exclusion STANDS and supersedes a blanket resume instruction). Migrated from glm_peak_pause_resume.py''s HARD_EXCLUDE_LANES constant.'),
  ('cc-fleet-health',    ARRAY['anthropic']::text[], false, 'glm-rollout-plan-20261013 line 35',
     'The SRE identity itself is never moved onto GLM, whatever a pointer file says. Migrated from glm_peak_pause_resume.py''s HARD_EXCLUDE_LANES constant.')
ON CONFLICT (lane) DO NOTHING;
