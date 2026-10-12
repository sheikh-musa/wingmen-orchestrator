-- 104_provider_policy.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- provider_policy — per-client model-provider allow-list (Musa op#29321 +
-- op#29324, via orch-console #63773/#63780, 2026-10-12). The gate the GLM /
-- non-Anthropic routing reads BEFORE placing a lane on z.ai. Replaces the old
-- blanket "never client-data on GLM" hold (orch-console #58195) with an
-- explicit per-client policy.
--
-- SAFETY (fail-closed) is carried on BOTH sides:
--   * resolver (scripts/lib/provider_policy.py): an UNKNOWN client_key -> Anthropic-only.
--   * this table's CHECKs (defence in depth):
--       - allowed_providers must always contain 'anthropic' (the overflow/fallback
--         target — GLM>=95% -> Claude keeps working);
--       - a non-Anthropic provider is allowed ONLY with text_only=true (Musa's
--         HARD condition: vision/image MCP off in code, the 2026-10-07 #58192
--         China image-store finding).
--
-- Seed rows cite the operator op# that cleared each client. finance/coffeemedia/
-- mamadah were UNCLASSIFIED in data_provenance; their GLM clearance here is the
-- EXPLICIT operator call op#29321, not a data-scope inference.

CREATE TABLE IF NOT EXISTS provider_policy (
  client_key        text PRIMARY KEY,
  allowed_providers text[]      NOT NULL DEFAULT ARRAY['anthropic']::text[],
  text_only         boolean     NOT NULL DEFAULT false,
  cleared_by        text,
  evidence          text,
  created_by        text        NOT NULL DEFAULT 'cc-fleet-health',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT provider_policy_anthropic_always
    CHECK ('anthropic' = ANY(allowed_providers)),
  CONSTRAINT provider_policy_nonanthropic_requires_text_only
    CHECK (allowed_providers <@ ARRAY['anthropic']::text[] OR text_only = true)
);

COMMENT ON TABLE provider_policy IS
  'Per-client model-provider allow-list (Musa op#29321/#29324). Fail-closed: unknown key => Anthropic-only (resolver). Non-Anthropic provider requires text_only=true.';

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
