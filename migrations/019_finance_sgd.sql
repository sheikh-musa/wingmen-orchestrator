-- 019_finance_sgd.sql — operator directive (console #2301): finance tracking
-- in SGD. USD-billed vendors converted @ 1.35 SGD/USD until invoice-confirmed;
-- claude-max is natively 300 SGD (operator #2300). fx refreshed at the monthly
-- review. Apply via scripts/apply_finance_sgd.py.

ALTER TABLE finance_subscriptions RENAME COLUMN amount_usd_month TO amount_sgd_month;

COMMENT ON COLUMN finance_subscriptions.amount_sgd_month IS
  'Monthly-normalized SGD (operator directive 2026-07-05 #2301). USD-billed vendors @ 1.35 fx until invoice-confirmed.';

UPDATE finance_subscriptions SET
  amount_sgd_month = CASE name
    WHEN 'claude-max'    THEN 300.00   -- native SGD, operator-confirmed
    WHEN 'anthropic-api' THEN 4.05
    WHEN 'supabase-pro'  THEN 60.75
    WHEN 'vercel'        THEN 27.00
    WHEN 'domains'       THEN 3.40
    WHEN 'zoho-mail'     THEN 1.35
    WHEN 'do-vps-sgp1'   THEN 43.20
    ELSE amount_sgd_month
  END,
  updated_at = now();

COMMENT ON VIEW finance_burn IS 'All figures SGD/month since migration 019 (2026-07-05).';
