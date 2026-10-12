-- 100_tablet_onboarding_telemetry.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#28967 (Musa, P1, orch-console spec #62739/#62764/#62784/#62845/#62850): PII-free two-way
-- telemetry for apps/tablet-onboarding (the standalone Emirates-ID-scanning PWA). This is
-- diagnostic ops data about the SCANNING DEVICE and the OCR pipeline's own behavior -- never
-- tenant data, never card content -- so it lands here (polled by orch-console the same way
-- agent_messages is), not in cosem-platform's multi-tenant/RLS world.
--
-- DEFENSE IN DEPTH (orch-console #62850): client-side masking alone is one bug away from an
-- unmasked field reaching this table. These CHECK constraints are the second, independent gate --
-- /api/telemetry (the first gate: strict allowlist + size caps + reject-on-mask-failure) cannot
-- insert a row that violates them even if its own validation has a bug. created_at truncates to
-- the minute (finer timestamps + a per-install id are themselves a re-identification vector).
--
-- raw_payload is the SERVER-ALLOWLISTED object actually inserted (post-validation, unknown keys
-- already dropped) -- NOT a copy of whatever the client posted. The typed columns are a
-- query-friendly projection of that same already-masked, already-checked data, not a separate
-- trust boundary from it.
--
-- masked_screenshot_url, when present, points at a private/unguessable blob URL (never a public
-- one) whose image content was produced by an html2canvas capture of a DOM CLONE that had every
-- data-pii element and camera/card media element replaced before render (src/telemetry/screenshot.ts)
-- -- masked by construction, not by a column-level promise. Retention: rows + the blobs they
-- reference are intended to auto-expire after 30 days; the cleanup job is a documented follow-up
-- (not yet built as of this migration -- tracked, see op#28967 handoff) rather than blocking this
-- schema, since kill-switch-OFF means zero rows exist to retain until orch-console reviews a
-- sample and flips it on.

create table if not exists tablet_onboarding_telemetry (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default date_trunc('minute', now()),
  install_id uuid not null,
  app_version text not null,
  build_sha text not null,
  event text not null,
  side text check (side is null or side in ('front', 'back')),
  stage text,
  duration_ms integer check (duration_ms is null or duration_ms >= 0),
  timed_out boolean,
  confidence real check (confidence is null or (confidence >= 0 and confidence <= 1)),
  band_count integer check (band_count is null or band_count >= 0),
  crop_geometry jsonb,
  id_masked text check (id_masked is null or id_masked ~ '^784-####-#######-#$'),
  id_checksum_valid boolean,
  name_arabic jsonb check (
    name_arabic is null or (
      (name_arabic - array['found', 'wordCount', 'script']) = '{}'::jsonb
      and jsonb_typeof(name_arabic -> 'found') = 'boolean'
      and jsonb_typeof(name_arabic -> 'wordCount') = 'number'
      and name_arabic ->> 'script' in ('latin', 'arabic', 'mixed', 'none')
    )
  ),
  name_english jsonb check (
    name_english is null or (
      (name_english - array['found', 'wordCount', 'script']) = '{}'::jsonb
      and jsonb_typeof(name_english -> 'found') = 'boolean'
      and jsonb_typeof(name_english -> 'wordCount') = 'number'
      and name_english ->> 'script' in ('latin', 'arabic', 'mixed', 'none')
    )
  ),
  device_metrics jsonb,
  masked_screenshot_url text check (masked_screenshot_url is null or masked_screenshot_url ~ '^https://'),
  raw_payload jsonb not null
);

comment on table tablet_onboarding_telemetry is
  'op#28967: PII-free diagnostics from the tablet-onboarding scanning PWA. raw_payload is the server-allowlisted object actually inserted, not the raw posted body -- see migration header.';

alter table tablet_onboarding_telemetry enable row level security;

create policy tablet_onboarding_telemetry_service_only on tablet_onboarding_telemetry
  for all
  to service_role
  using (true)
  with check (true);

create policy tablet_onboarding_telemetry_console_ro on tablet_onboarding_telemetry
  for select
  to console_readonly
  using (true);
