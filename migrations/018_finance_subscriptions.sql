-- 018_finance_subscriptions.sql — financial architecture (operator directive
-- console #2299): every recurring subscription/infra cost is a substrate row;
-- monetization floors derive from the finance_burn view. Rule going forward:
-- the row is created BEFORE a new subscription is taken (registry-first).
-- Apply via scripts/apply_finance_subscriptions.py (decision-962: no db push).

CREATE TABLE IF NOT EXISTS finance_subscriptions (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL UNIQUE,       -- 'claude-max', 'supabase-pro', ...
    vendor           text NOT NULL,
    category         text NOT NULL,              -- ai | infra | domains | tooling
    plan             text,
    amount_usd_month numeric(8,2) NOT NULL,      -- monthly-normalized (annual/12)
    billing_cycle    text NOT NULL DEFAULT 'monthly',   -- monthly | annual | usage
    status           text NOT NULL DEFAULT 'active',    -- active | upcoming | cancelled
    owner            text NOT NULL DEFAULT 'wingmen',   -- wingmen | client | partner
    confirmed        boolean NOT NULL DEFAULT false,    -- operator-confirmed amount
    notes            text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE finance_subscriptions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON finance_subscriptions FROM anon, authenticated;

CREATE OR REPLACE VIEW finance_burn AS
SELECT
    sum(amount_usd_month) FILTER (WHERE status='active'  AND owner='wingmen')                    AS active_burn,
    sum(amount_usd_month) FILTER (WHERE status='active'  AND owner='wingmen' AND confirmed)      AS active_burn_confirmed,
    sum(amount_usd_month) FILTER (WHERE status='upcoming' AND owner='wingmen')                   AS upcoming_burn,
    count(*)              FILTER (WHERE status='active'  AND owner='wingmen' AND NOT confirmed)  AS unconfirmed_rows
FROM finance_subscriptions;

INSERT INTO finance_subscriptions (name, vendor, category, plan, amount_usd_month, billing_cycle, status, owner, confirmed, notes) VALUES
 ('claude-max',        'Anthropic',  'ai',      'Max 20x (assumed)',   200.00, 'monthly', 'active',   'wingmen', false, 'Fleet runs on Max via CLAUDE_CODE_OAUTH_TOKEN. CONFIRM: tier (5x $100 / 20x $200) and seat count (fleet + operator personal?).'),
 ('anthropic-api',     'Anthropic',  'ai',      'metered',               3.00, 'usage',   'active',   'wingmen', false, 'Shipforge serving COGS — real per-gen logging in worker costs.jsonl (single-pass $0.149-0.256 measured). Scales with customers; unit economics in 2026-07-05 pressure test.'),
 ('supabase-pro',      'Supabase',   'infra',   'Pro org',              45.00, 'monthly', 'active',   'wingmen', false, 'DISCOVERED: org lqbojdqwgzgxhioezfgb plan=pro; 3 active projects (orchestrator ap-se-2, ihsanos ap-se-1, wingmen-personal ap-se-1). $25 base + ~3x micro compute - $10 credit ~= $45 est. CONFIRM from invoice. goumlyne = Gazzabyte org (partner-billed, $0 to us).'),
 ('vercel',            'Vercel',     'infra',   'Pro (assumed)',        20.00, 'monthly', 'active',   'wingmen', false, 'DISCOVERED teams: wingmen-aa9356e1 + personal. Plan not exposed via API. CONFIRM Pro vs Hobby; shipforge pilots deploy here.'),
 ('firebase-gcp',      'Google',     'infra',   'Blaze',                 0.00, 'usage',   'active',   'client',  false, 'cosem-adcda hosting/Firestore. CONFIRM owner: COSEM/client-billed vs wingmen card.'),
 ('domains',           'various',    'domains', NULL,                    2.50, 'annual',  'active',   'wingmen', false, 'wingmen.dev + ihsanos.com (+any others — CONFIRM full list). Annualized /12.'),
 ('zoho-mail',         'Zoho',       'tooling', 'Mail',                  1.00, 'monthly', 'active',   'wingmen', false, 'Wingmen email cutover (reminder existed 07-02). CONFIRM plan.'),
 ('github',            'GitHub',     'tooling', 'Free',                  0.00, 'monthly', 'active',   'wingmen', true,  'Free tier; Actions billing deliberately bypassed (direct deploys per 07-03).'),
 ('tailscale',         'Tailscale',  'infra',   'Personal',              0.00, 'monthly', 'active',   'wingmen', true,  'Free personal tailnet.'),
 ('telegram-cloudflare','various',   'infra',   'Free',                  0.00, 'monthly', 'active',   'wingmen', true,  'Bot API + tunnels free tiers.'),
 ('do-vps-sgp1',       'DigitalOcean','infra',  'Droplet (planned)',    32.00, 'monthly', 'upcoming', 'wingmen', false, 'Linux migration target ~Jul 21-Aug 15 (WINGMEN-STRATEGY-001 R2); est 4GB-8GB droplet $24-48.')
ON CONFLICT (name) DO NOTHING;
