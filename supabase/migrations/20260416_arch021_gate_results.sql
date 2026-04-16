-- ARCH-021: Add gate result columns to work_outputs
-- Gate 1 = commit existence check; Gate 2 = intent alignment (Haiku)
-- Both are nullable JSONB — NULL means the gate did not run (commit_expected=false, or no decision_text)

alter table work_outputs
  add column if not exists gate1_result jsonb,
  add column if not exists gate2_result jsonb;
