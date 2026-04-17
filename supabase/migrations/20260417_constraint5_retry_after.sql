-- Constraint 5 (Resource Bounds): retry_after for rate-limited job re-queuing.
-- When Claude API rate limit is detected, jobs are re-queued with retry_after=NOW()+30min
-- so pick_next_jobs skips them until the backoff window passes.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_after TIMESTAMPTZ;
