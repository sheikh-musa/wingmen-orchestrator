-- 104_provider_policy.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_table_privilege anon public.provider_policy SELECT
-- assert: no_table_privilege authenticated public.provider_policy SELECT
-- assert: no_table_privilege anon public.provider_policy UPDATE
-- assert: no_table_privilege authenticated public.provider_policy UPDATE
-- assert: no_table_privilege anon public.provider_policy INSERT
-- assert: no_table_privilege authenticated public.provider_policy INSERT
--
-- provider_policy — per-client model-provider allow-list (Musa op#29321 +
-- op#29324, via orch-console #63773/#63780, 2026-10-12). The gate the GLM /
-- non-Anthropic routing reads BEFORE placing a lane on z.ai. Replaces the old
-- blanket "never client-data on GLM" hold (orch-console #58195) with an
-- explicit per-client policy.
--
-- This is a CONTROL table, not telemetry: it decides whether a lane may route
-- to z.ai. So it is locked TIGHTER than a data table (orch-console #63805) —
-- RLS + REVOKE the pg_default_acl anon/authenticated grants. WITHOUT the REVOKE,
-- a holder of the anon API key could UPDATE irsyad's row to ['anthropic','zai'],
-- text_only=true (which passes both CHECKs), putting the one HARD residency line
-- (irsyad never on z.ai) behind the anon key.
--
-- SAFETY (fail-closed) is carried on THREE sides:
--   * resolver (scripts/lib/provider_policy.py): UNKNOWN client_key -> Anthropic-only.
--   * CHECKs: 'anthropic' always present (overflow/fallback target); a non-Anthropic
--     provider is allowed ONLY with text_only=true (Musa's HARD condition -- vision/
--     image MCP off in code, the 2026-10-07 #58192 China image-store finding).
--   * RLS/REVOKE: only service_role writes; console reads; anon/authenticated locked out.
--
-- Seed rows cite the operator op# that cleared each client. finance/coffeemedia/
-- mamadah were UNCLASSIFIED in data_provenance; their GLM clearance here is the
-- EXPLICIT operator call op#29321, not a data-scope inference. Re-run-safe.

CREATE TABLE IF NOT EXISTS provider_policy (
  client_key        text PRIMARY KEY,
  allowed_providers text[]      NOT NULL DEFAULT ARRAY['anthropic']::text[],
  text_only         boolean     NOT NULL DEFAULT false,
  cleared_by        text,
  evidence          text,
  created_by        text        NOT NULL DEFAULT 'cc-fleet-health',
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT provider_policy_anthropic_always
    CHECK ('anthropic' = ANY(allowed_providers)),
  CONSTRAINT provider_policy_nonanthropic_requires_text_only
    CHECK (allowed_providers <@ ARRAY['anthropic']::text[] OR text_only = true)
);

COMMENT ON TABLE provider_policy IS
  'Per-client model-provider allow-list (Musa op#29321/#29324). Fail-closed: unknown key => Anthropic-only (resolver). Non-Anthropic provider requires text_only=true. Control table: service_role-only writes (RLS), console read-only.';

-- RLS + REVOKE (control-table lockdown, same shape as 096/103). Re-run-safe.
ALTER TABLE public.provider_policy ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS provider_policy_service_only ON public.provider_policy;
CREATE POLICY provider_policy_service_only ON public.provider_policy
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS provider_policy_console_ro ON public.provider_policy;
CREATE POLICY provider_policy_console_ro ON public.provider_policy
  FOR SELECT TO console_readonly USING (true);
REVOKE ALL ON public.provider_policy FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.provider_policy TO console_readonly;
GRANT ALL ON public.provider_policy TO service_role;

INSERT INTO provider_policy (client_key, allowed_providers, text_only, cleared_by, evidence) VALUES
  ('irsyad',       ARRAY['anthropic']::text[],        false, 'Musa op#29321',
     'irsyad = anthropic-only, HARD (op#29321 via orch-console #63773). REAL NRIC PII silo; never z.ai.'),
  ('cosem-adcda',  ARRAY['anthropic','zai']::text[],  true,  'Musa op#29324',
     'ADCDA allowed on GLM text-only (op#29324 "data goes through singapore", via #63780). REAL gov-ID PII; vision/image MCP off in code. Lanes mixing ADCDA+other cosem count as ADCDA.'),
  ('cosem',        ARRAY['anthropic','zai']::text[],  true,  'Musa op#29321',
     'cosem NON-ADCDA work on GLM text-only (op#29321 via #63773).'),
  ('internal',     ARRAY['anthropic','zai']::text[],  true,  'Musa op#29321',
     'internal/fleet-infra lanes on GLM text-only (op#29321). No client data.'),
  ('coffeemedia',  ARRAY['anthropic','zai']::text[],  true,  'Musa op#29321',
     'coffeemedia on GLM text-only (op#29321). Explicit operator clearance; data_provenance row not yet registered.'),
  ('finance',      ARRAY['anthropic','zai']::text[],  true,  'Musa op#29321',
     'finance on GLM text-only (op#29321). Explicit operator clearance; data_provenance row not yet registered.'),
  ('mamadah',      ARRAY['anthropic','zai']::text[],  true,  'Musa op#29321',
     'mamadah on GLM text-only (op#29321). Explicit operator clearance; data_provenance row not yet registered.')
ON CONFLICT (client_key) DO NOTHING;
