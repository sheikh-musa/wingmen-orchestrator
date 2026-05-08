-- CAI-RESP-154: boot_briefing substrate visibility
-- Bundles three ratified signals (CAI-RESP-153 P2 cc_session_costs +
-- RALPH-STATE-VISIBILITY-001 ralph_state + CAI-RESP-151 P3
-- stale_unresponded_count_12h) into one atomic boot_briefing migration.
--
-- This file ships sections 1-4 (tables + seed). Sections 5 (view extension)
-- and 6 (assertion gate) are appended in a subsequent commit on the same
-- branch.
--
-- Idempotent: ADD TABLE IF NOT EXISTS, INSERT ON CONFLICT DO NOTHING.
-- Pre-apply per CAI-RESP-102.

BEGIN;

-- ============================================================================
-- Section 1: boot_briefing_config — small key-value config table
-- Per CAI-RESP-154 Q3 amendment: outlier threshold lives here so future
-- tuning is one UPDATE, not a migration.
-- ============================================================================
CREATE TABLE IF NOT EXISTS boot_briefing_config (
    key         TEXT PRIMARY KEY,
    value_int   INTEGER,
    value_text  TEXT,
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO boot_briefing_config (key, value_int, notes)
VALUES (
    'cc_session_costs_outlier_token_threshold',
    50000,
    'cc_session_costs sessions exceeding (input_tokens + output_tokens) over this in 24h surface as boot_briefing outlier rows. Tune via UPDATE.'
)
ON CONFLICT (key) DO NOTHING;

-- ============================================================================
-- Section 2: cc_session_costs — per-session token-spend visibility
-- Per CAI-RESP-153 P2 (visibility-only-30-day window) + CAI-RESP-154 Q1.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cc_session_costs (
    id                       BIGSERIAL PRIMARY KEY,
    cc_identity              TEXT NOT NULL,
    sub_tag                  TEXT,
    session_id               TEXT,
    started_at               TIMESTAMPTZ NOT NULL,
    ended_at                 TIMESTAMPTZ,
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    source                   TEXT NOT NULL,
    has_per_message_detail   BOOLEAN NOT NULL DEFAULT false,
    notes                    TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cc_session_costs_identity_started
    ON cc_session_costs (cc_identity, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_cc_session_costs_recent
    ON cc_session_costs (started_at DESC);

COMMENT ON TABLE cc_session_costs IS
    'Per-CC-session token-spend visibility per CAI-RESP-153 P2. '
    'Visibility-only window through 2026-06-08 (30d from CAI-RESP-153 ratification); '
    'revisit policy after.';

COMMENT ON COLUMN cc_session_costs.has_per_message_detail IS
    'true → cc_session_messages rows present for this session_cost_id. '
    'false (default) → only top-level totals; common for cai sessions where '
    'per-message visibility is estimation-tier per CAI-RESP-154 Q7.';

-- ============================================================================
-- Section 3: cc_session_messages — per-message detail child table
-- Dormant when has_per_message_detail=false on parent. Per CAI-RESP-154 Q1.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cc_session_messages (
    id                BIGSERIAL PRIMARY KEY,
    session_cost_id   BIGINT NOT NULL REFERENCES cc_session_costs(id) ON DELETE CASCADE,
    sequence_no       INTEGER NOT NULL,
    role              TEXT NOT NULL,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    occurred_at       TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cc_session_messages_parent
    ON cc_session_messages (session_cost_id, sequence_no);

-- ============================================================================
-- Section 4: ralph_state — single-row operational state
-- Per RALPH-STATE-VISIBILITY-001 + CAI-RESP-154 Q4 with operator-confirmed
-- seed values (since=2026-04-29 SGT, FILTER-002 dropped from gates because
-- it shipped 2026-05-08).
-- ============================================================================
CREATE TABLE IF NOT EXISTS ralph_state (
    id                      INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    state                   TEXT NOT NULL CHECK (state IN ('active', 'paused')),
    since                   TIMESTAMPTZ NOT NULL,
    paused_reason           TEXT,
    resume_gates            TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    last_state_change_by    TEXT NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger: paused_reason required when state='paused'
CREATE OR REPLACE FUNCTION ralph_state_validate()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.state = 'paused' AND (NEW.paused_reason IS NULL OR length(trim(NEW.paused_reason)) = 0) THEN
        RAISE EXCEPTION 'ralph_state.paused_reason required when state=''paused''';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- linter-allow: DROP TRIGGER immediately followed by CREATE TRIGGER same name — idempotent recreate pattern per PR #30 spec
DROP TRIGGER IF EXISTS ralph_state_validate_trigger ON ralph_state;
CREATE TRIGGER ralph_state_validate_trigger
    BEFORE INSERT OR UPDATE ON ralph_state
    FOR EACH ROW EXECUTE FUNCTION ralph_state_validate();

-- Seed initial paused state
INSERT INTO ralph_state (id, state, since, paused_reason, resume_gates, last_state_change_by)
VALUES (
    1,
    'paused',
    '2026-04-29 00:00:00+08'::timestamptz,
    'bugs raised through apps pipeline are not automatically butchered by ralph; pipeline still maturing',
    ARRAY[
        'BUG-PIPELINE-SYNTHETIC-FILTER-001-enforce-cutover',
        'CAI-PROCESS-AUTO-ANNOUNCE-FIX-001',
        'OPTION-B-VERIFICATION-WORKER-READINESS',
        'DOMAIN-SPECIFIC-CC-CONCERNS'
    ],
    'operator-musa'
)
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE ralph_state IS
    'Single-row operational state per RALPH-STATE-VISIBILITY-001. CAI consults '
    'state before urgency framing. Operator-only state changes (cc-orchestrator '
    'does NOT auto-resume).';

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260509120000',
    'boot_briefing_substrate_visibility',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
