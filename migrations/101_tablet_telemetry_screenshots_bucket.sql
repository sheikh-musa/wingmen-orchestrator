-- 101_tablet_telemetry_screenshots_bucket.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#28967: follow-up to migration 100 (orch-console #62852 required any file change get a new
-- gate, so this is split out rather than amending the already-gated 100). PRIVATE bucket for
-- masked screenshots (never public=true) -- api/telemetry-screenshot.ts generates a time-limited
-- signed URL per upload, which is what tablet_onboarding_telemetry.masked_screenshot_url actually
-- stores; nothing in this bucket is ever reachable by a bare URL. 2MB cap and a single allowed
-- mime type close off this bucket being repurposed for anything else.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('tablet-telemetry-screenshots', 'tablet-telemetry-screenshots', false, 2097152, array['image/png'])
on conflict (id) do nothing;
