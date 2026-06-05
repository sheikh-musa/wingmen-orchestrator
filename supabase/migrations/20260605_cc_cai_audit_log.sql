-- CAI-RESP-185 HARD SHIP CONDITION: every cc-cai-daemon tool call writes to
-- this table BEFORE side effects. Operator-auditable. INV-5 amanah trail.
--
-- Per CAI-RESP-185: "It is the precondition that makes autonomous reading/
-- classification of the operator message stream halal to run unattended."
-- This table is amanah-load-bearing — no other Phase 1 module may write a
-- side effect before its corresponding audit row has landed here.

BEGIN;

CREATE TABLE IF NOT EXISTS cc_cai_audit_log (
    id                        BIGSERIAL PRIMARY KEY,
    logged_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id                TEXT NOT NULL,           -- daemon-restart-scoped uuid
    event_type                TEXT NOT NULL,           -- 'classification' | 'tool_call' | 'escalation' | 'silent_action' | 'kill_switch_trip'
    agent_message_id          BIGINT,                  -- FK-soft to agent_messages.id when applicable
    classification            TEXT,                    -- 'mark_read_fyi' | 'ack_fyi' | 'escalate' | NULL
    classification_reason     TEXT,                    -- human-readable rationale
    confidence                NUMERIC(3,2),            -- 0.00-1.00; below threshold triggers INV-6 HOLD
    tool_name                 TEXT,                    -- 'supabase_update_read_at' | 'telegram_send' | 'sdk_query' | etc.
    tool_input_summary        JSONB,                   -- redacted inputs (NO operator PII unless agent_messages.body is operator-authored)
    tool_output_summary       JSONB,                   -- truncated output / status
    escalated_to_operator     BOOLEAN NOT NULL DEFAULT false,
    telegram_message_id       BIGINT,                  -- when escalation pushed to telegram
    kill_switch_state         TEXT NOT NULL DEFAULT 'live'  -- 'live' | 'pure_escalation_mode' | 'panic_disabled'
);

COMMENT ON TABLE cc_cai_audit_log IS
    'CAI-RESP-185 INV-5 hard ship condition: every cc-cai-daemon classifier '
    'decision, tool call, and escalation logged BEFORE side effects. Makes '
    'autonomous reading/classification of operator message stream halal to '
    'run unattended (amanah-bearing precondition).';

CREATE INDEX IF NOT EXISTS idx_ccal_logged_at
    ON cc_cai_audit_log (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccal_session
    ON cc_cai_audit_log (session_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccal_event_type
    ON cc_cai_audit_log (event_type, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccal_msg_id
    ON cc_cai_audit_log (agent_message_id)
    WHERE agent_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ccal_escalated
    ON cc_cai_audit_log (logged_at DESC)
    WHERE escalated_to_operator = true;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_name='cc_cai_audit_log'
    ) THEN RAISE EXCEPTION 'cc_cai_audit_log missing'; END IF;
    RAISE NOTICE 'CAI-RESP-185 INV-5 audit table verified';
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260605120000', 'cc_cai_audit_log',
    ARRAY[
        $stmt$CREATE TABLE IF NOT EXISTS cc_cai_audit_log (id BIGSERIAL PRIMARY KEY, logged_at TIMESTAMPTZ NOT NULL DEFAULT now(), session_id TEXT NOT NULL, event_type TEXT NOT NULL, agent_message_id BIGINT, classification TEXT, classification_reason TEXT, confidence NUMERIC(3,2), tool_name TEXT, tool_input_summary JSONB, tool_output_summary JSONB, escalated_to_operator BOOLEAN NOT NULL DEFAULT false, telegram_message_id BIGINT, kill_switch_state TEXT NOT NULL DEFAULT 'live')$stmt$
    ]::text[]
)
ON CONFLICT (version) DO NOTHING;
