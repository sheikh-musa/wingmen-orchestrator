-- 021: operator_messages.cos_triage — PASSIVE Chief-of-Staff triage annotation.
-- Step 1 of reports/chief-of-staff-one-front-door-spec.md (Phase 2 / CoS).
--
-- ADDITIVE + BACKWARD-COMPATIBLE + ZERO BEHAVIOR CHANGE. A single nullable jsonb
-- column that holds the passive triage suggestion for an inbound operator
-- message: {category, suggested_route, confidence, rationale, domain, tier, v}.
-- Computed by nervous_system/triage.py (a PURE, deterministic function) and
-- READ by Nazim when he reconciles (operator_log.unprocessed()/recent() append
-- it). It does NOT route, does NOT auto-act, changes NO send/gate/route path.
--
-- Dead-man's-switch (spec §Step 1): if the classifier errors, the column stays
-- NULL and today's manual behavior is unchanged. Reversible: stop reading it.
-- Backfill is OPTIONAL (older rows read as NULL → triage recomputed at read time
-- from the stored text, so nothing is lost).
--
-- Applied live via direct psycopg (decision-962; NEVER `supabase db push`).
-- Idempotent: ADD COLUMN IF NOT EXISTS.
ALTER TABLE operator_messages ADD COLUMN IF NOT EXISTS cos_triage jsonb;
COMMENT ON COLUMN operator_messages.cos_triage IS
  'Passive CoS triage suggestion (nervous_system/triage.py). Read-only annotation; no routing effect. Nullable.';
