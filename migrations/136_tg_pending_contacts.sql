-- 136_tg_pending_contacts.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- TG2.0 section 6 (design report 2026-10-17, build approved orch-console #86250).
-- One row per unclaimed chat that has messaged a bot group, so the reviewer can
-- be alerted ONCE and can answer with a button. This table holds no message
-- content: identity + state + expiry only.
--
-- Why it is not built on migration 131's audited-row pattern: that pattern
-- protects durable, append-only governance records. This is transient
-- operational state whose whole lifecycle is bounded by expires_at — rows are
-- created, resolved by a button press, and become inert. `resolved_at` /
-- `resolved_by` are the audit trail, and the bus row an Approve files carries
-- the durable record of the decision.
--
-- Gates this migration does NOT touch: it creates nothing in bot_channels, so
-- it cannot enable, allowlist, or grant anything. Approve files a gate request
-- and the normal onboarding gates still run on it.
--
-- GATED: apply with scripts/apply_migration.py --gate after orch-console's GO.
-- Nothing here is applied by this PR.

BEGIN;

CREATE TABLE IF NOT EXISTS public.tg_pending_contacts (
    id             bigserial PRIMARY KEY,
    bot_username   text        NOT NULL,
    chat_id        text        NOT NULL,
    channel_tag    text        NOT NULL,
    first_name     text,
    username       text,
    pending_id     text        NOT NULL,
    notified_at    timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    state          text        NOT NULL DEFAULT 'pending',
    resolved_at    timestamptz,
    resolved_by    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tg_pending_contacts_state_check
        CHECK (state IN ('pending', 'approved', 'ignored')),
    CONSTRAINT tg_pending_contacts_resolution_check
        CHECK ((state = 'pending') = (resolved_at IS NULL))
);

-- The opaque id that travels in callback_data (never the raw chat_id).
CREATE UNIQUE INDEX IF NOT EXISTS tg_pending_contacts_pending_id_key
    ON public.tg_pending_contacts (pending_id);

-- At most ONE live pending row per chat: the fire-once guarantee is a
-- constraint, not a code convention, so two concurrent inbounds cannot both
-- alert.
CREATE UNIQUE INDEX IF NOT EXISTS tg_pending_contacts_one_pending_per_chat
    ON public.tg_pending_contacts (bot_username, chat_id)
    WHERE state = 'pending';

-- The lookup the ingest makes on every unclaimed inbound.
CREATE INDEX IF NOT EXISTS tg_pending_contacts_chat_lookup
    ON public.tg_pending_contacts (bot_username, chat_id, created_at DESC);

COMMENT ON TABLE public.tg_pending_contacts IS
    'TG2.0 s6: unclaimed chats awaiting the reviewer''s Approve/Ignore. Holds '
    'identity and state only, never message content; bounded by expires_at.';

COMMIT;
