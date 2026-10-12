-- 102_tablet_telemetry_retention.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#28967 (orch-console #62852 condition 4, #62934, split per #62948): 30-day retention for
-- tablet_onboarding_telemetry ROWS ONLY. A raw SQL `delete from storage.objects` only removes the
-- metadata row -- Supabase does NOT delete the underlying stored file that way, so the masked
-- screenshots this bucket holds need a real Storage API call (storage.from(bucket).remove(...)),
-- not SQL, or they'd become orphaned blobs that look cleaned up but aren't. That half ships
-- separately as a scheduled job hitting the Storage API directly (see op#28967 handoff notes),
-- not in this migration -- a plain Postgres table is the only thing a cron SQL DELETE is actually
-- correct for.

select cron.schedule(
  'tablet_onboarding_telemetry_30d_ttl',
  '20 4 * * *',
  $$
    delete from tablet_onboarding_telemetry
     where created_at < now() - interval '30 days'
  $$
);
