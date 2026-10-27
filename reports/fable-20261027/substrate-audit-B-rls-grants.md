# Audit slice B — table/view/sequence exposure (substrate tscuymavysscrvoberrr), 2026-10-28, READ-ONLY (op#34969)

Method: joined dump/relations.tsv x table_grants.tsv x policies.tsv (analyze.py), then proved each claim live via `ro` (default_transaction_read_only=on). Live pg_policies count = 289 (107 with anon/public in roles) — matches dump. No client rows printed; counts are pg_stat n_live_tup.

Role facts (live): `select rolname, rolcanlogin, rolbypassrls from pg_roles ...` → service_role and postgres have rolbypassrls=true (so "service_role grant w/o policy" is NOT a deny-all; 34 such tables are fine). anon/authenticated/console_readonly/share_readonly/cto_desktop/exec_broker_role/exec_runner/fleet_reaper all bypassrls=false. FORCE RLS = 0/180 tables; all owned by postgres.

## 1. EFFECTIVE anon / authenticated exposure

### B-1 [HIGH, latent] anon still holds INSERT/UPDATE/DELETE grants on 66 public tables incl. payments, donations, persons, clients, profiles, accounts, receipts — migration 032 (CAI-RESP-512 revoke) was NEVER applied
Live effect today = denied by RLS (every policy on these tables is auth.uid()/org-scoped or service_role-jwt; anon has no uid → 0 rows, with_check fails). But the grant layer is the only thing standing between "one mis-written `USING (true)` policy" and a live hole, and the fleet's own doctrine (032 header) says to remove it.
Proof:
```
select count(distinct table_name), string_agg(distinct table_name,' ') from information_schema.role_table_grants
 where table_schema='public' and grantee='anon' and privilege_type in ('INSERT','UPDATE','DELETE');
-- 66 | accounts anchor_batches ... clients ... donations ... payments ... persons profiles ... receipts ... ui_events user_unlocks ...
select table_name, string_agg(privilege_type,',') from information_schema.role_table_grants where table_schema='public' and grantee='anon'
 and table_name in ('accounts','clients','donations','payments','persons','profiles','receipts') group by 1;
-- all 7: INSERT,SELECT,UPDATE,DELETE,REFERENCES,TRIGGER
-- ledger: dump/migration_ledger.tsv has 031_anon_write_truncate_lockdown.sql, NO row for 032_anon_write_revoke_nontelemetry.sql
```
Root cause: default ACL `postgres public r {anon=rxtm, authenticated=arwdxtm}` + `supabase_admin public r {anon=arwdDxtm,...}` — every new table auto-grants anon/authenticated. Even if 032 were applied, new tables regress unless the default ACL is also changed.
Remediation (do not execute): apply 032 through the gated applier (cai re-confirm sha), AND `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE INSERT,UPDATE,DELETE ON TABLES FROM anon` (same for supabase_admin default). Keep `GRANT INSERT ON ui_events TO anon` only.

### B-2 [HIGH] work_item (3,905 rows) + work_item_audit (6,405 rows): RLS OFF, anon SELECT, authenticated full DML — live, not latent
These are the fleet's work-item SSOT (migration 114) and its audit trail: owner_agent/title/source_ref/drop_reason columns (no client PII by schema, but it is the operational ledger agents act on). Any holder of the anon key via PostgREST can read the whole table; any `authenticated` JWT can INSERT/UPDATE/DELETE rows, including the audit trail.
```
select relname, relrowsecurity from pg_class where relname in ('work_item','work_item_audit');  -- False, False
select has_table_privilege('anon','public.work_item','SELECT'),               -- true
       has_table_privilege('authenticated','public.work_item','INSERT'),      -- true
       has_table_privilege('authenticated','public.work_item','UPDATE'),      -- true
       has_table_privilege('authenticated','public.work_item','DELETE'),      -- true
       has_table_privilege('authenticated','public.work_item_audit','DELETE');-- true
-- n_live_tup: work_item 3905, work_item_audit 6405. migrations/114_work_item_ssot.sql contains no ENABLE ROW LEVEL SECURITY / GRANT / REVOKE.
```
Remediation: `ALTER TABLE work_item, work_item_audit ENABLE ROW LEVEL SECURITY`; add `*_service_only` (service_role ALL) + `*_console_ro` (console_readonly SELECT) policies; `REVOKE ALL ON work_item, work_item_audit FROM anon, authenticated`.

### B-3 [MEDIUM] ui_events: anon INSERT with `WITH CHECK (true)` — unauthenticated, unbounded write; anon also holds UPDATE/DELETE grants that 032 would have removed
By design (CAI-512 sanctioned telemetry ingest), but with_check=true means any anon caller can insert arbitrary `user_id`/`repo`/`properties` jsonb — spoofable attribution + storage DoS. Table currently 0 rows. UPDATE/DELETE grants are dead (no policy) but should go.
```
select policyname, roles, cmd, with_check from pg_policies where tablename='ui_events';
-- anyone_can_insert_events {public} INSERT true ; service_role_reads_events {public} SELECT auth.role()='service_role'
select has_table_privilege('anon','public.ui_events','INSERT');  -- true
-- grants: anon ui_events INSERT,SELECT,UPDATE,DELETE,REFERENCES,TRIGGER
```
Remediation: tighten with_check (e.g. `repo = any(allowed) and user_id is null or user_id = auth.uid()`), add a rate/size CHECK (`length(properties::text) < 4096`), `REVOKE UPDATE, DELETE ON ui_events FROM anon`.

### B-4 [LOW, by design] anon+authenticated open SELECT (`USING (true)`) on reference/Quran/hadith corpora and audit-attestation tables
asbab_nuzul, ayah_meta, ayah_themes, ayat, ayat_topics, hadith_collections, hadiths, mutashabihat, tafsir_entries, topics, word_morphology, juridical_texts/translations/embeddings (1,880 rows), ruling_audit_log (296), ingestion_provenance (12), daily_attestations, audit_key_registry, portfolio_entries, site_content; organizations slug-lookup (`slug is not null and deleted_at is null`, 0 rows). authenticated additionally: mizan_retract_gate, project_governance (6), project_governance_audit (12).
Proof: `select tablename, policyname, qual from pg_policies where ('anon'=any(roles) or 'public'=any(roles) or 'authenticated'=any(roles)) and cmd='SELECT' and qual='true'`.
Note: ruling_audit_log, audit_key_registry, daily_attestations, project_governance(_audit) are governance/audit surfaces readable by anyone with the anon key — confirm that is intended (public-verifiability design) rather than accidental.

### Not findings (verified): the apparent anon/authenticated INSERT paths on mamadah_*, vault_secrets, vault_secrets_history, vault_access_log, ingest_poll_health are `FOR ALL USING (false)` with no WITH CHECK → PG uses USING as the check → denied. All 82 (anon) / 95 (authenticated) remaining policies are auth.uid()/org/jwt-scoped — ihsanos-style tenant policies, correct shape. 10 RLS-on tables with anon full grants and NO policy at all (accounts, asbab_embeddings, change_requests, claim_tokens, hadith_embeddings, onboarding_events, site_links, sites, tafsir_embeddings, user_unlocks) are effective deny-all today — grant cleanup only (covered by B-1).

## 2. RLS-OFF tables (6)
| table | rows | grants | verdict |
|---|---|---|---|
| work_item | 3905 | anon r, authenticated arwd, service_role all | **B-2 HIGH** |
| work_item_audit | 6405 | anon r, authenticated arwd, service_role all | **B-2 HIGH** |
| exec_broker_access_log | 35 | exec_broker_role INSERT,SELECT,UPDATE only | LOW — no anon/auth grant (`has_table_privilege('anon',...,'SELECT')`=false). Note the broker holds UPDATE on its own append-only log (draft hc95 line 33 acknowledges; harden with a BEFORE UPDATE trigger or revoke). |
| invariant_assertion_runs | 72 | console_readonly SELECT, service_role INSERT,SELECT | LOW — anon false; RLS-off is harmless given grants, but enable for consistency |
| share_lane_labels | 7 | service_role only | LOW |
| share_pool_map | 1 | service_role only | LOW |
Proof: `select relname, relacl from pg_class where relkind='r' and not relrowsecurity and relnamespace='public'::regnamespace`.

## 3. Deny-all mismatches (custom roles)
### B-5 [MEDIUM, intent broken] exec_broker_role has SELECT on agent_messages but NO policy → reads 0 rows
Draft hc95 says the broker needs agent_messages SELECT "to re-check a gate_id". With RLS on and no `{exec_broker_role}` policy the re-check silently returns nothing — either the broker fails closed on every gate (functional break) or the code path isn't exercised. Also: this grant has no ledgered migration (only `migrations/drafts/`), i.e. hand-applied drift.
```
select has_table_privilege('exec_broker_role','public.agent_messages','SELECT');  -- true
select policyname, roles from pg_policies where tablename='agent_messages';
-- agent_messages_console_ro{console_readonly}, agent_messages_service_only{service_role}, console_readonly_select_messages{console_readonly}, cto_desktop_insert_self{cto_desktop}, cto_desktop_read_own{cto_desktop}  → none for exec_broker_role
```
Remediation: add `agent_messages_exec_broker_ro` SELECT policy scoped to gate rows (e.g. `message_type='gate' or to_agent='exec-broker'`), ledger it; or drop the grant if the re-check moved elsewhere.

### B-6 [MEDIUM, intent broken] exec_runner has SELECT on strategic_decisions with NO policy → deny-all; no migration provenance found in repo
```
select has_table_privilege('exec_runner','public.strategic_decisions','SELECT');  -- true
select policyname, roles from pg_policies where tablename='strategic_decisions';  -- 2 policies, none for exec_runner
```
Remediation: decide intent; add `strategic_decisions_exec_runner_ro` or revoke. Ledger whichever.

### B-7 [LOW, reverse mismatch] tablet_onboarding_telemetry has `tablet_onboarding_telemetry_console_ro` policy but console_readonly has NO grant → console cannot read it
```
select has_table_privilege('console_readonly','public.tablet_onboarding_telemetry','SELECT');  -- false
```
Remediation: `GRANT SELECT ON tablet_onboarding_telemetry TO console_readonly` (gated, verify via READER DSN).

Clean: console_readonly (70 policies, all matched by grants), share_readonly, cto_desktop (2), fleet_reaper (4+2 shared), exec_runner's other 2 tables — grant+policy pairs intact. 12 tables carry `deny_all_* {public} ALL false` with no anon/auth grant — belt-and-braces, fine.

## 4. Views (19, all owned by postgres = bypassrls owner)
```
select c.relname, c.reloptions, c.relacl from pg_class c where relnamespace='public'::regnamespace and relkind='v';
```
security_invoker ON (12): agent_message_stale_claims, agent_observed_activity, cto_council_open_sessions, finance_burn, fleet_drain_freshness, fleet_proposal_metrics_v, held_commitments_due, lane_tasks_state, open_blocking_tasks, operator_ask_turnaround_daily, stale_agents, stale_blockers.
security_invoker OFF (7, definer-semantics, run as postgres → bypass RLS): boot_briefing, data_provenance_flags, decision_audit_state, inbox_sla_violations, invariant_registry_state, share_lane_status_v, share_pool_status_v.

### B-8 [LOW] anon/authenticated hold SELECT (+INSERT/UPDATE/DELETE grant bits `arwdxtm`) on views cto_council_open_sessions and stale_blockers
Both are security_invoker=true, so underlying RLS applies: cto_council_sessions policy is `auth.role()='service_role'` → anon gets 0 rows; stale_blockers reads field_events where anon has NO table grant → permission denied. Effective exposure: none. But the grants are dead/misleading and the write bits on a view are nonsense.
```
select has_table_privilege('anon','public.stale_blockers','SELECT');            -- true
select has_table_privilege('anon','public.cto_council_open_sessions','SELECT'); -- true
select grantee from information_schema.role_table_grants where table_name='field_events' and grantee='anon'; -- 0 rows
```
Remediation: `REVOKE ALL ON cto_council_open_sessions, stale_blockers FROM anon, authenticated`.

### B-9 [LOW/context] definer views readable by non-bypass roles: decision_audit_state + invariant_registry_state (console_readonly), share_lane_status_v + share_pool_status_v (share_readonly)
These run as postgres and would expose underlying rows regardless of RLS. console_readonly already has `_console_ro USING(true)` on strategic_decisions/invariant_registry, and share_pool_map/share_lane_labels are RLS-off + pool_usage — so no privilege escalation today, but the pattern silently widens if a future policy narrows console_readonly. boot_briefing: only postgres/service_role (`has_table_privilege('anon','public.boot_briefing','SELECT')`=false for anon and authenticated) — fine.
Remediation: `ALTER VIEW ... SET (security_invoker=true)` on the 4 non-service views where console/share roles already have matching policies; leave boot_briefing (service-only).

## 5. Sequences
### B-10 [LOW] anon + authenticated hold rwU (USAGE/SELECT/UPDATE) on 80 of 84 public sequences via default ACL
Lets any anon-key caller call `nextval()`/`setval()` on e.g. agent_messages_id_seq / strategic_decisions_id_seq → sequence burn or forced id collisions (ties into the sequence-drift class the fleet already self-heals).
```
select has_sequence_privilege('anon','public.agent_messages_id_seq','UPDATE');        -- true
select has_sequence_privilege('authenticated','public.strategic_decisions_id_seq','USAGE'); -- true
-- default_acl: postgres public S {anon=rwU, authenticated=rwU}; supabase_admin public S same
```
Remediation: `REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated` + `ALTER DEFAULT PRIVILEGES ... REVOKE ... ON SEQUENCES FROM anon, authenticated`; keep USAGE for authenticated only on sequences behind legitimate authenticated INSERT policies (mizan_*).

## 6. FORCE ROW LEVEL SECURITY
Context/LOW: 0/180 tables have relforcerowsecurity; all are owned by postgres (rolbypassrls=true) and the orch/launchd daemons connect as postgres (DATABASE_URL). RLS is therefore a no-op for every first-party code path; only PostgREST roles and the custom read roles are actually gated. Not fixable by FORCE alone (bypassrls overrides FORCE) — the real control is the body-separated DSNs (console RO reader etc.) already in use.

## Ranked summary
- HIGH B-2: work_item / work_item_audit RLS OFF, anon read + authenticated write, ~10k rows, live.
- HIGH B-1 (latent): migration 032 never applied; anon still has write grants on 66 tables incl. payments/donations/persons/clients/profiles; default ACL re-grants on every new table.
- MEDIUM B-3: ui_events anon INSERT with_check=true (spoofable, unbounded). B-5/B-6: exec_broker_role→agent_messages and exec_runner→strategic_decisions grants with no policy (intent broken, unledgered).
- LOW B-4 public-read corpora/audit tables (confirm intent), B-7 console policy w/o grant, B-8 dead anon view grants, B-9 definer views, B-10 anon sequence rwU, FORCE RLS absent.
