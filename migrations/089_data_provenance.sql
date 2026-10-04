-- 089_data_provenance.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- assert: no_table_privilege anon public.data_provenance SELECT
-- assert: no_table_privilege authenticated public.data_provenance SELECT
-- assert: no_table_privilege anon public.data_provenance_flags SELECT
-- assert: no_table_privilege authenticated public.data_provenance_flags SELECT
-- assert: no_execute anon public.classify_data_provenance(text,text)
-- assert: no_execute authenticated public.classify_data_provenance(text,text)
-- assert: search_path public.classify_data_provenance(text,text)
--
-- SEARCH_PATH FIX (cc-storefront opus confirmation pass, bus #51791, F1 — LOW
-- severity, required): classify_data_provenance() is SECURITY DEFINER with no
-- `SET search_path` and referenced `data_provenance` unqualified. A SQL secdef
-- function is not inlined and resolves unqualified names against the CALLER's
-- search_path at call time — a shadowing relation earlier in that path would be
-- read instead of the real table. Fixed to match this store's secdef convention
-- (063/064, including the directly-comparable governance functions
-- project_governance/cai_gate): `SET search_path = ''` + schema-qualified
-- `public.data_provenance` in the body, verified by the `-- assert: search_path`
-- line above.
--
-- GRANT-HYGIENE FIX (cc-quality review, bus #51770, MEDIUM finding on an
-- earlier revision of this file): the RLS policy below had no `TO` clause,
-- which Postgres defaults to PUBLIC/FOR ALL — despite its name, it granted
-- USING(true) to every role including anon/authenticated, converting
-- "RLS + grant defense-in-depth" into no defense at all. Combined with this
-- store's Supabase-seeded default privileges (anon/authenticated inherit
-- SELECT/EXECUTE on every new object, migration 049's own doctrine note),
-- the table, the data_provenance_flags view (owner-rights, bypasses RLS),
-- and the SECURITY DEFINER classify function would all have been
-- anon-readable on apply. Fixed per this store's own CAI-1018 convention
-- (migrations/049, 068, 088): explicit `FOR ALL TO service_role`, then
-- REVOKE ALL from anon/authenticated on every new object, verified by the
-- `-- assert:` lines above (CAI-RESP-1397 #5 — an unverified REVOKE can be
-- a silent no-op).
--
-- Musa directive (bus #51657/#51670, orch-console relay, 2026-10-05): a single
-- source of truth for whether a store/org/tenant's rows are REAL client data or
-- SYNTHETIC/test data — "a quran for agents". Triggered by a false data-security
-- P0 raised over generated test data. classify() never infers from a slug/name:
-- "demo-academy" (no "-synthetic" suffix) is cosem org 1478c9b2, which is the
-- real-intended client org currently holding a synthetic trainee set — the trap
-- this table exists to prevent is inferring REAL from the absence of "-synthetic"
-- in a name, or SYNTHETIC from the presence of "demo" in a name.
--
-- MIXED_PENDING_REAL: added beyond the three states in #51657 because #51670
-- surfaced a real case that doesn't fit REAL/SYNTHETIC/MIXED cleanly — an org
-- whose current rows are synthetic but which is slated to receive real client
-- data at a go-live gate (docs/GO-LIVE-CHECKLIST.md). Collapsing that into
-- plain SYNTHETIC would re-create the same false-alarm risk in the other
-- direction once real data lands and the row isn't updated.
--
-- NOTE (orch-console gate conditions, bus #51717): this migration does NOT
-- touch boot_briefing. The 'data_provenance_flag' arm lands separately via
-- scripts/extend_boot_briefing_arm.py, which builds the new view body from
-- the LIVE pg_get_viewdef() at apply time (decision 962 — never from a
-- hardcoded copy baked into a migration file) and is applied as its own
-- gated step.

create table if not exists data_provenance (
  id              bigserial primary key,
  project_ref     text not null,
  org_id          text not null default '',  -- '' = the answer when a caller queries org_id=''
                                               -- (no org given); NOT inherited by other orgs on the
                                               -- same project_ref — an unseeded specific org still
                                               -- resolves UNCLASSIFIED, not this row (cc-quality
                                               -- review, bus #51770: safe direction, but don't call
                                               -- it a "store-level default that applies project-wide").
                                               -- NOT null, because unique(project_ref, org_id) would not
                                               -- dedupe multiple NULLs under standard SQL NULL semantics
  org_name        text,
  alias           text not null,        -- LAYER-VOCAB-001 human label, e.g. "cosem-platform demo/dev"
  classification  text not null check (classification in ('REAL', 'SYNTHETIC', 'MIXED', 'MIXED_PENDING_REAL')),
  evidence        text not null,        -- must cite a script/commit/migration/bus-msg id — never a slug or name
  owner           text,
  created_by      text not null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),  -- NO auto-bump trigger (cc-quality review,
                                                          -- bus #51770/#51772 item 4): a caller doing
                                                          -- `UPDATE data_provenance SET classification=...`
                                                          -- MUST also set `updated_at = now()` explicitly,
                                                          -- or the staleness signal classify() surfaces
                                                          -- goes wrong. See docs/GO-LIVE-CHECKLIST.md's
                                                          -- example UPDATE for the pattern to follow.
  unique (project_ref, org_id)
);

alter table data_provenance enable row level security;
drop policy if exists "service role full access" on data_provenance;
create policy "service role full access" on data_provenance
  for all to service_role using (true) with check (true);

create index if not exists idx_data_provenance_project_ref on data_provenance(project_ref);
create index if not exists idx_data_provenance_classification on data_provenance(classification);

revoke all on data_provenance from public, anon, authenticated;

-- classify(): the one sanctioned lookup. No row -> callers (scripts/data_truth.py)
-- construct UNCLASSIFIED client-side and treat it as REAL (fail-safe direction,
-- orch-console gate condition #2) — this function itself just returns nothing.
create or replace function classify_data_provenance(p_project_ref text, p_org_id text default '')
returns table (classification text, evidence text, owner text, alias text, updated_at timestamptz)
language sql stable security definer
set search_path = '' as $$
  select classification, evidence, owner, alias, updated_at
  from public.data_provenance
  where project_ref = p_project_ref
    and org_id = coalesce(p_org_id, '')
  limit 1;
$$;

revoke all on function classify_data_provenance(text, text) from public, anon, authenticated;
grant execute on function classify_data_provenance(text, text) to service_role;

-- Lean boot-context arm (ARCH-019 doctrine: index only, no full dump): surfaces
-- only the rows an agent actually needs to see unprompted — anything not a
-- plain REAL/SYNTHETIC, since those are exactly the ones worth a second look
-- before anyone raises or dismisses a data-security alarm. Consumed by the
-- boot_briefing 'data_provenance_flag' arm (wired separately, see note above).
create or replace view data_provenance_flags as
select project_ref, org_id, org_name, alias, classification, evidence, owner, updated_at
from data_provenance
where classification in ('MIXED', 'MIXED_PENDING_REAL')
order by project_ref, org_id;

revoke all on data_provenance_flags from public, anon, authenticated;

-- Seed rows. orch-console gate condition #3 (bus #51717): on the irsyad silo
-- (goumlynecruxrlmzlntp) and the ihsanos multi-tenant DB (ceayjeamtmcyzzvqflus),
-- the STORE-LEVEL DEFAULT is REAL and NO org-level SYNTHETIC row is seeded here
-- — "fixture"/wetprove-looking orgs on those stores have turned out to be real
-- clients before (reference_wetprove_fixture_orgs_are_real_clients). Any
-- org-level SYNTHETIC row for those two stores must be added later by
-- cc-irsyad-coord, with their own evidence and sign-off — not by this migration.
insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('tscuymavysscrvoberrr', '', null, 'orchestrator substrate',
   'REAL', 'fleet operational store (jobs/build_log/agent_messages/strategic_decisions) — not client data, no synthetic-vs-real ambiguity', 'orch', 'cc-substrate'),

  ('ceayjeamtmcyzzvqflus', '', null, 'ihsanos multi-tenant DB',
   'REAL', 'multi-tenant production DB per docs/data-store-registry.md; store-level default per orch-console gate condition #3 — any org-level SYNTHETIC row here requires cc-irsyad-coord evidence + sign-off, not seeded here', 'orch', 'cc-substrate'),

  ('goumlynecruxrlmzlntp', '', null, 'irsyad silo (goumlyne)',
   'REAL', 'irsyad production silo per docs/data-store-registry.md; store-level default per orch-console gate condition #3 (bus #51717) — known-synthetic-looking orgs (e.g. QA Madrasah, QA Jumaat) are NOT seeded SYNTHETIC here; that requires cc-irsyad-coord evidence + sign-off (fixture-looking orgs on this store have turned out to be real clients before)', 'irsyad-coord', 'cc-substrate'),

  ('brrgastulcffamlbggyu', '', null, 'wingmen-personal',
   'REAL', 'per docs/data-store-registry.md; never a demo target', 'orch', 'cc-substrate'),

  ('ywrpttpxwfcoodovxhsr', '', null, 'cosem-platform demo/dev',
   'MIXED', 'holds both synthetic-by-design demo orgs and org 1478c9b2 (see per-org row) — store-level default is MIXED, not REAL, precisely because of the 1478c9b2 case', 'cosem', 'cc-substrate'),

  ('ywrpttpxwfcoodovxhsr', '1478c9b2', 'Civil Defense Academy', 'cosem org 1478c9b2 (slug demo-academy)',
   'MIXED_PENDING_REAL',
   'trainee rows (61+1) are SYNTHETIC: scripts/reseed-adcda-groups.ts commit b7491a6, run 2026-07-22 (+1 on 08-30), fixed FIRST/LAST name arrays, registers 101-830, seed-script header says "SYNTHETIC data only" (bus #51664/#51670, op#25626). This org is the REAL-intended client at go-live (docs/GO-LIVE-CHECKLIST.md gates real data on removing one-click logins first). Prior CAI-RESP-1340 containment note (docs/data-store-registry.md) describes a real-PII finding from 2026-07-09, BEFORE this reseed — both facts are preserved, not contradictory: real data was present, then this org was reseeded synthetic for the current demo/dev cycle. Classification must NEVER be inferred from the slug (no "-synthetic" suffix) — that is the exact trap op#25626 exists to correct.',
   'cosem', 'cc-substrate'),

  ('ywrpttpxwfcoodovxhsr', 'ba98da04', null, 'cosem org ba98da04 (slug demo-academy-synthetic)',
   'SYNTHETIC', 'bus #51664/#51670: explicitly synthetic demo org, no real-data intent registered', 'cosem', 'cc-substrate')
on conflict (project_ref, org_id) do nothing;
