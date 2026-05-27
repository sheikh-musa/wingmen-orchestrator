-- CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME: extend cc_session_costs with
-- cache-token columns for proper cost modeling. Cache-read is much cheaper
-- than fresh-input; existing summed input_tokens conflates them.
--
-- Additive. Idempotent. Pre-apply per CAI-RESP-102.

BEGIN;

ALTER TABLE cc_session_costs
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE cc_session_costs
    ADD COLUMN IF NOT EXISTS cache_read_input_tokens INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN cc_session_costs.cache_creation_input_tokens IS
    'Per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME: fresh tokens written to '
    'prompt cache (5m + 1h ephemeral). Distinct from input_tokens (uncached '
    'fresh input) for cost-model accuracy.';

COMMENT ON COLUMN cc_session_costs.cache_read_input_tokens IS
    'Per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME: tokens served from '
    'prompt cache hits. Heavily discounted vs fresh input on Anthropic '
    'pricing — track separately for accurate cost attribution.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='cc_session_costs'
                      AND column_name='cache_creation_input_tokens') THEN
        RAISE EXCEPTION 'cache_creation_input_tokens missing after ADD COLUMN';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='cc_session_costs'
                      AND column_name='cache_read_input_tokens') THEN
        RAISE EXCEPTION 'cache_read_input_tokens missing after ADD COLUMN';
    END IF;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260527120000', 'cc_session_costs_cache_tokens', ARRAY[]::text[])
ON CONFLICT (version) DO NOTHING;
