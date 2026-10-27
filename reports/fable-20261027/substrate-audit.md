# Substrate database audit — tscuymavysscrvoberrr (read-only)

- **Ask:** Musa op#34969 via orch-console bus #78604, thread a92508a7-e56d-4f28-bc60-2df6d7cc2cc8.
- **Auditor:** cc-substrate-2 (temporary Fable 5.1 lane) + 4 parallel subagents. 2026-10-27 19:11Z–20:50Z.
- **Scope:** orchestrator substrate only (ref `tscuymavysscrvoberrr`, PostgreSQL 17.6). Nothing else touched.
- **Method:** every query ran through a runner that sets `default_transaction_read_only=on` at session level and rolls back (a `CREATE TABLE` probe failed with `ReadOnlySqlTransaction` before any audit query ran). No DDL, no DML, no migrations. No raw client rows reproduced here (CAI-1034): counts, names and schema only.
- **Inventory (public schema):** 180 tables · 19 views · 265 functions (34 SECURITY DEFINER) · 37 triggers · 84 sequences · 289 RLS policies · 6 pg_cron jobs · 21 auth.users (0 active in 30d).

## 0. Ranked findings (top first)

| # | Sev | Finding | Section |
|---|-----|---------|---------|
| 1 | CRITICAL | `post_agent_message` SECURITY DEFINER RPC is executable by `anon`/`authenticated` with no caller validation: anyone holding the project anon key can forge bus rows as any agent (incl. `orch-console`/`cai`), set P1+requires_response (wakes the hub), and the provenance trigger stamps `posted_by_identity='postgres'` exactly like legit rows. | §2.1 |
| 2 | HIGH | `mark_delivered` / `ack_message` SECDEF RPCs open to anon: suppress delivery or forge acks on any bus row by id. Root cause: migration 117 revoked from `PUBLIC` only, but Supabase default ACL gives anon/authenticated their OWN explicit EXECUTE rows at CREATE time (the exact lesson migration 059 documents). | §2.1, §2.2 |
| 3 | HIGH | `sweep_stale_agents(interval,text,text)` executable by PUBLIC/anon: caller-chosen threshold (`'0'` reaps the whole fleet to offline), free-text `reaped_by`, bypasses the agent_status identity trigger by design. Migration 010 revoked it; a later CREATE OR REPLACE re-opened it (the 052 lesson). | §2.1 |
| 4 | HIGH | Outbound SSRF primitives callable by anon over PostgREST: `public.http_get/http_post` (pgsql-http, synchronous), `public.dblink/dblink_exec`, `net.http_get/http_post` + `net.worker_restart` (anon has USAGE on `net`). Infra migration `cai1004_revoke_pg_net_*` was written but never applied. | §2.3 |
| 5 | HIGH | `work_item` (3,906 rows) + `work_item_audit` (6,410 rows): RLS OFF, anon SELECT, authenticated INSERT/UPDATE/DELETE. The fleet work-item SSOT and its audit trail are readable with the anon key and writable by any of the 21 `auth.users` JWTs. Migration 114 created them with no RLS/REVOKE, so default ACL applied. | §3.1 |
| 6 | HIGH (latent) | Migration 032 (CAI-RESP-512 anon-write revoke) was never applied: anon still holds INSERT/UPDATE/DELETE grants on 64 public tables incl. `payments, donations, persons, clients, profiles, accounts, receipts`. Blocked by RLS today; one future `USING(true)` policy or `DISABLE ROW LEVEL SECURITY` turns it live. Default ACL re-creates the grant on every new table (work_item is the proof). | §3.2 |
| 7 | HIGH (structural) | Every first-party process (hub, console, all lanes, launchd daemons; 215 `DATABASE_URL` references) connects as `postgres` (`rolbypassrls=true`, owner of every table). RLS, policies and the bus RPC are not a boundary for lanes at all: `bus_send.py` is a direct INSERT. The only lane-vs-bus control is process discipline + PreToolUse hooks. | §1 |
| 8 | MEDIUM | `wingmen_config` holds a JWT-shaped secret as a row (`cto_strategist_jwt`) read by SECDEF `trigger_cto_strategist` for `net.http_post`; anon has full DML grants on the table (RLS is the only gate). Belongs in Vault. | §2.4 |
| 9 | MEDIUM | Deny-all mismatches: `exec_broker_role` SELECT on `agent_messages` with NO policy (broker gate re-check reads 0 rows; grant exists only in `migrations/drafts/`), `exec_runner` SELECT on `strategic_decisions` with no policy and no migration provenance, `tablet_onboarding_telemetry_console_ro` policy without a GRANT. | §3.3 |
| 10 | MEDIUM | `ui_events`: anon INSERT `WITH CHECK (true)`, spoofable `user_id`, unbounded `properties`; anon also keeps dead UPDATE/DELETE grants. | §3.4 |
| 11 | MEDIUM | `exec_sql` RPC is called from `nervous_system/council_agent.py` and `council_executor.py` but does not exist in the DB. Dead code today; under the current default ACL, "fixing" it would ship anon-callable arbitrary SQL. | §2.5 |
| 12 | LOW | anon + authenticated hold USAGE/SELECT/UPDATE on 80 of 84 public sequences incl. `agent_messages_id_seq` (`setval` from the anon key forces id collisions). | §3.5 |
| 13 | LOW | 7 of 19 views run with definer semantics (no `security_invoker`), incl. `decision_audit_state`, `invariant_registry_state`, `share_*_status_v`: harmless today, bypasses any future RLS narrowing. Dead anon `arwdxtm` grants on views `cto_council_open_sessions`, `stale_blockers`. | §3.6 |
| 14 | LOW | 6 trigger-only SECDEF functions carry anon EXECUTE (not directly invocable), incl. `sync_org_memberships_to_jwt` (writes `auth.users`); 32/34 SECDEF pin `search_path=public` but mostly without `pg_temp`. | §2.6 |

Sections 4 (hygiene, sequences, residency, cron) and 5 (migration ledger and drift) add their own rows below the table in §4/§5; the appendices carry every proving query with live output.

## 1. Structural context: who actually connects as what

Proof (`pg_stat_activity` at 19:17Z): `postgres` ×8 via Supavisor (hub/console/lanes/daemons), `console_readonly` ×10 (fleet console), `authenticator` ×6 (PostgREST = the anon/service keys are live), `supabase_admin` (realtime, cron, exporter).

```sql
select rolname, rolbypassrls, rolsuper from pg_roles where rolname in ('postgres','service_role','anon','authenticated','console_readonly');
-- postgres: bypassrls=t  service_role: bypassrls=t  anon/authenticated/console_readonly: f
select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' and c.relforcerowsecurity;  -- 0
```

Consequences the rest of this report is read against:
- **For first-party code** (every lane, hub, console, launchd job: 215 `DATABASE_URL` refs across `scripts/`, `nervous_system/`, root) there is no database-level boundary at all. "A lane bypassing the bus RPC" is the normal path: `scripts/bus_send.py` is a direct `INSERT INTO agent_messages`; `my_inbox.py` is a direct UPDATE. The 34 SECDEF functions, 289 policies and leases are honoured by convention and PreToolUse hooks only. Not a bug in any one object; a design fact the operator should hold in mind when reading "RLS denies this today" below.
- **For the public surface** (anon key in frontends/webhooks, authenticated JWTs for the 21 `auth.users`, service key), PostgREST exposes `public` and the RPCs. That is where findings #1–#6 are exploitable.
- **console_readonly** is the only first-party role that RLS actually constrains (`default_transaction_read_only=on`, `statement_timeout=10s`), which is why the GRANT+policy pairing rule exists.

## 2. Functions (slice A, full detail in `substrate-audit-A-functions.md`)

### 2.1 Bus RPCs open to anon and authenticated (#1, #2, #3)

```sql
select p.proname, has_function_privilege('anon',p.oid,'EXECUTE') anon_x,
       has_function_privilege('authenticated',p.oid,'EXECUTE') auth_x
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='public' and p.proname in ('post_agent_message','mark_delivered','ack_message','sweep_stale_agents');
-- all four: anon_x=t auth_x=t
select count(*) filter (where has_function_privilege('anon',p.oid,'EXECUTE')) anon_exec,
       count(*) filter (where p.prosecdef and has_function_privilege('anon',p.oid,'EXECUTE')) anon_exec_secdef, count(*) total
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public';
-- 241 | 20 | 265
```

`post_agent_message` body is a plain INSERT of its nine parameters; `p_from_agent`, `p_to_agent`, `p_priority`, `p_requires_response` are caller-controlled and nothing compares them to a JWT claim, `identity_allowlist` (0 rows) or `agent_status`. `populate_agent_messages_provenance` stamps `posted_by_identity = current_user`, which inside a SECDEF owned by postgres is `postgres`: the same value as 62,876 legitimate rows, so a forged row is forensically indistinguishable. An anon caller can therefore (a) post `from_agent='orch-console'` P1+requires_response to `cc-orchestrator` and wake the hub, (b) post as `cai`, (c) flood `work_item` through the mirror trigger. `mark_delivered`/`ack_message` are `UPDATE … WHERE id = p_id` with no ownership predicate. `sweep_stale_agents('0','anyone',null)` reaps every agent to offline and bypasses `enforce_agent_status_identity` by design via `set_config`.

Repo grep: no production caller of these RPCs (only tests, `ci_bootstrap_schema.py`, `audit_mac_mini.py`); first-party code uses direct SQL as postgres. So the REVOKE is zero-impact.

**Remediation (not executed):** `REVOKE EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text), mark_delivered(bigint,integer), ack_message(bigint), sweep_stale_agents(interval,text,text) FROM PUBLIC, anon, authenticated;` then `GRANT … TO service_role`. Apply through the gated applier; then fix the default ACL (§2.2) or the next `CREATE OR REPLACE` re-opens them.

### 2.2 Root cause: migration 117 revoked from PUBLIC only; default ACL grants anon its own row (#2)

```sql
select grantee from information_schema.role_routine_grants where routine_name='mark_delivered';
-- anon, authenticated, postgres, service_role     (PUBLIC is absent: 117's REVOKE did run)
select defaclrole::regrole, defaclobjtype, defaclacl::text from pg_default_acl where defaclnamespace='public'::regnamespace;
-- postgres f {postgres=X,anon=X,authenticated=X,service_role=X}
-- postgres r {postgres=arwdDxtm,anon=rxtm,authenticated=arwdxtm,service_role=arwdDxtm}
-- postgres S {…anon=rwU,authenticated=rwU…}   (+ supabase_admin rows with anon=arwdDxtm on r)
```

`migrations/117_durable_inbox_ack_additive.sql:138-143` runs `REVOKE … FROM PUBLIC` and `GRANT … TO service_role`; the ledger shows it applied 2026-10-24. Because the default ACL writes an explicit `anon=X` and `authenticated=X` at CREATE time, revoking PUBLIC leaves those rows intact. `migrations/059_close_bulk_insert_morphology_anon_exec.sql:38-50` documents precisely this ("PUBLIC IS IN THE REVOKE LIST — the exact F2 lesson from 058… anon/authenticated held EXPLICIT grants"), and `052:26-27` documents that CREATE OR REPLACE re-applies the default. 117 regressed a lesson the repo already recorded twice; 010's revoke of `sweep_stale_agents` was undone the same way by a later redefinition. Review did not catch it because nothing tests `has_function_privilege('anon', …)` after apply.

**Remediation:** `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;` (and the same for `supabase_admin`, tables and sequences), plus a one-time `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated` with an explicit re-grant allowlist (Quran-corpus readers, `auth_user_org_ids*` RLS helpers, anything a frontend truly calls). Add the §2.1 query to the applier's post-apply check.

### 2.3 Outbound network primitives callable with the anon key (#4)

```sql
select n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), has_function_privilege('anon',p.oid,'EXECUTE')
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where p.proname in ('http_get','http_post','dblink','dblink_exec','worker_restart');
-- public.http_get(uri varchar) t · public.http_post(uri,content,content_type) t · public.dblink(text) t · public.dblink_exec(text) t · net.http_get/http_post t · net.worker_restart t
select has_schema_privilege('anon','net','USAGE');  -- t
select extname, extnamespace::regnamespace from pg_extension where extname in ('http','dblink','pg_net');  -- all in public
```

`public.http_get(uri)` has a named parameter, so `POST /rest/v1/rpc/http_get {"uri":"http://169.254.169.254/…"}` with the anon key is a synchronous SSRF from the database egress IP; `dblink` allows outbound libpq connections (credential probing of other silos by hostname). `migrations/infra/cai1004_revoke_pg_net_public_anon_authenticated.sql` is the written fix for `net.*` and has no `migration_ledger` row; the pgsql-http and dblink extensions were never addressed.

**Remediation:** apply cai1004 via the applier; `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated` covers http/dblink; consider moving `http`/`dblink` to the `extensions` schema (Supabase default) so they are not REST-exposed at all.

### 2.4 Secret stored as a config row (#8)

```sql
select count(*) filter (where key ilike '%jwt%' or key ilike '%token%' or key ilike '%secret%') secretish, count(*) from wingmen_config;  -- 1 | 3
select has_table_privilege('anon','public.wingmen_config','SELECT');  -- t (grant); RLS policy auth.role()='service_role' is the only gate
```

`trigger_cto_strategist` (SECDEF) reads `cto_strategist_jwt` from `wingmen_config` for `net.http_post`. One permissive policy away from exposure; belongs in `vault.secrets` (extension present, v0.3.1).

### 2.5 `exec_sql` ghost RPC (#11)

`nervous_system/council_agent.py:173`, `council_executor.py:315,394,431` call `.rpc('exec_sql', …)`; `select count(*) from pg_proc where proname='exec_sql'` → 0. Dead today. Under the current default ACL, anyone who "fixes" the missing function creates anon-callable arbitrary SQL. Delete the call sites or guard with a test that asserts the function must not exist.

### 2.6 Hygiene (#14)

- 6 trigger-only SECDEF functions carry anon EXECUTE (`sync_org_memberships_to_jwt` writes `auth.users`, `trigger_cto_strategist`, `project_governance_*`, `populate_*_provenance`, `enforce_audit_tier_change_guard`): not invocable via REST (trigger signature) but closed by the §2.2 bulk revoke anyway.
- 32/34 SECDEF pin `search_path=public`; only 5 add `pg_temp`. anon/authenticated hold TEMP on the database. Exploitation needs raw SQL, so LOW; add `, pg_temp` on next touch. The two unpinned are C-language `dblink_connect_u`, supabase_admin-only.
- No dynamic SQL (`EXECUTE`/`format()`) in any of the 265 bodies.

## 3. Tables, policies, views, sequences (slice B, full detail in `substrate-audit-B-rls-grants.md`)

### 3.1 `work_item` / `work_item_audit`: RLS off and exposed (#5)

```sql
select relname, relrowsecurity, has_table_privilege('anon','public.'||relname,'SELECT') anon_sel,
       has_table_privilege('authenticated','public.'||relname,'DELETE') auth_del
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and relkind='r' and not relrowsecurity order by 1;
-- exec_broker_access_log f f f · invariant_assertion_runs f f f · share_lane_labels f f f · share_pool_map f f f
-- work_item               f t t · work_item_audit             f t t
select count(*) from auth.users;  -- 21 (0 signed in within 30d)
```

Migration 114 (`work_item_ssot`) has no ENABLE RLS / REVOKE, so the default ACL applied. Titles, owner agents, source refs and the full audit trail of the fleet's work-item SSOT are readable with the anon key; any authenticated JWT can insert/update/delete. The other four RLS-off tables are reachable only by their custom roles/service_role (verified false for anon). `exec_broker_access_log` has UPDATE for `exec_broker_role` on what the hc95 draft calls an append-only log.

**Remediation:** `ALTER TABLE work_item, work_item_audit ENABLE ROW LEVEL SECURITY; REVOKE ALL FROM anon, authenticated;` + `_console_ro` policies; revoke UPDATE on `exec_broker_access_log` from `exec_broker_role`.

### 3.2 Migration 032 never applied; anon write grants on 64 tables (#6)

```sql
select count(*) from pg_tables t where schemaname='public' and (has_table_privilege('anon','public.'||tablename,'INSERT')
   or has_table_privilege('anon','public.'||tablename,'UPDATE') or has_table_privilege('anon','public.'||tablename,'DELETE'));  -- 64
select migration_name from migration_ledger where migration_name like '03%';  -- 030, 031, 033, 034 … (no 032)
```

`migrations/032_anon_write_revoke_nontelemetry.sql` (CAI-RESP-512 item 3, "REVOKE INSERT/UPDATE/DELETE from anon AND PUBLIC on ALL public tables, keep ui_events") exists on disk, is numbered between two applied migrations, and has no ledger row. Live effect today is nil because every anon policy on those tables is `auth.uid()`/org/service-jwt scoped or `USING(false)` (verified for `agent_messages`, `strategic_decisions`, `operator_messages`, `held_commitments`: anon has no grant at all, revoked separately). But `payments, donations, persons, clients, profiles, accounts, receipts, audit_log, org_members, organizations` all carry `anon=arwdxtm`, and ten RLS-on tables (`accounts, claim_tokens, sites, site_links, onboarding_events, change_requests, user_unlocks, *_embeddings`) have anon full grants and zero policies. One future `USING(true)` policy or `DISABLE ROW LEVEL SECURITY` makes them live. The default ACL recreates the grant on every new table (work_item is the proof).

**Remediation:** apply 032 through the gated applier (re-verify its scope first: it predates ~30 tables), then `ALTER DEFAULT PRIVILEGES … REVOKE ALL ON TABLES FROM anon, authenticated` for both `postgres` and `supabase_admin`.

### 3.3 Deny-all mismatches (#9)

```sql
select g.grantee, g.table_name from information_schema.role_table_grants g
 where g.table_schema='public' and g.grantee in ('exec_broker_role','exec_runner','console_readonly','share_readonly','cto_desktop','fleet_reaper')
   and not exists (select 1 from pg_policies p where p.tablename=g.table_name and g.grantee = any(p.roles))
   and (select relrowsecurity from pg_class where oid=('public.'||g.table_name)::regclass);
-- exec_broker_role agent_messages · exec_runner strategic_decisions
select tablename, policyname from pg_policies where policyname like '%_console_ro' and not exists
  (select 1 from information_schema.role_table_grants g where g.table_name=pg_policies.tablename and g.grantee='console_readonly');
-- tablet_onboarding_telemetry tablet_onboarding_telemetry_console_ro
```

The exec-broker's gate_id re-check (`migrations/drafts/hc95_exec_broker_access_log.sql:77`) reads `agent_messages` and silently gets 0 rows; the GRANT exists only in a draft, i.e. hand-applied. `exec_runner`'s grant on `strategic_decisions` has no migration anywhere. All 70 `console_readonly` policies have matching grants (clean).

### 3.4 `ui_events` open anon insert (#10)

Policy `ui_events_anon_insert` is `WITH CHECK (true)`; `user_id` and `properties` are unbounded and spoofable; anon also keeps UPDATE/DELETE grant bits with no policy. Sanctioned telemetry, but add a `WITH CHECK (octet_length(properties::text) < 4096 and user_id is null or user_id = auth.uid())` shape and revoke the dead bits.

### 3.5 Sequences writable by anon (#12)

```sql
select count(*) from pg_sequences s where schemaname='public' and has_sequence_privilege('anon','public.'||sequencename,'UPDATE');  -- 80 of 84
select has_sequence_privilege('anon','public.agent_messages_id_seq','UPDATE');  -- t
```

`setval('agent_messages_id_seq', 1)` from the anon key would make every subsequent bus insert collide (the same failure class as the held_commitments #418/#419 drift, induced externally). Covered by the default-ACL fix.

### 3.6 Views (#13)

```sql
select c.relname, c.reloptions from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='v' and coalesce(array_to_string(c.reloptions,','),'') not like '%security_invoker=true%';
-- boot_briefing, decision_audit_state, invariant_registry_state, share_lane_status_v, share_pool_status_v, data_provenance_flags, inbox_sla_violations
```

Definer-semantics views owned by postgres read the base tables with bypassrls. Today their readers are postgres/service_role/console_readonly/share_readonly with matching `_console_ro` policies, so no escalation; but any later narrowing of a base-table policy is silently bypassed through the view. `cto_council_open_sessions` and `stale_blockers` carry anon `arwdxtm` grant bits (dead: `security_invoker=true` and base RLS deny). Set `security_invoker=true` on the seven; revoke the dead view grants.

Not findings after verification: the apparent anon INSERT paths on `vault_*`, `mamadah_*`, `ingest_poll_health` are `FOR ALL USING(false)` with no WITH CHECK, which Postgres treats as deny; `service_role` "grant without policy" rows are moot because service_role is bypassrls; open `USING(true)` SELECT on the Quran/hadith/juridical corpora and the attestation/ruling-audit tables matches their public-verifiability intent (confirm `ruling_audit_log` and `audit_key_registry` are meant to be world-readable).

## 4. Hygiene, sequences, residency, cron (slice C, full detail in `substrate-audit-C-hygiene-residency.md`)

Additional ranked rows (continuing the §0 table):

| # | Sev | Finding | Proof |
|---|-----|---------|-------|
| 15 | HIGH (hygiene) | pg_cron job 6 `agent_status_history_90d_ttl` has failed on every one of its 190 runs since 2026-04-21: it filters on `created_at` but the column is `recorded_at`. The table has never been trimmed: 699,032 rows, 176,709 past TTL (25%), 190 MB and growing. | `select count(*) filter (where status='failed'), count(*) from cron.job_run_details where jobid=6;` → 190/190, last message `column "created_at" does not exist` |
| 16 | HIGH if real / MEDIUM if seed | ihsanos-v0 person and financial tables live in the orchestrator substrate: `persons` 58 (41 emails, 29 phones), `donations` 31, `receipts` 31, `accounts` 9, `org_members` 23, `chat_history` 157, `auth.users` 21. April-2026 tenant plus a July-12 white-label batch, none carrying the irsyad org id. `scripts/residency_sweep.py` is org-id-keyed and does not look at this ref, so the standing gate cannot see it (TENANT-RESIDENCY-001). Seed-vs-real needs the owning lane to classify from seed scripts; this audit did not read rows. | `select count(*), count(email), count(phone) from persons;` → 58, 41, 29 |
| 17 | MEDIUM | ai-scholar corpus is ~1.0 GB (55% of the DB: `hadith_embeddings` 479 MB, `tafsir_embeddings` 299 MB…) with live writes to `mizan_interactions` and `ruling_audit_log` through 2026-10-23 and an `ai-scholar` repo migration in the ledger for this ref. The substrate is ai-scholar's store by default, not by a registry designation. | `select repo, migration_name from migration_ledger where silo_ref='tscuymavysscrvoberrr' and repo<>'orchestrator';` |
| 18 | MEDIUM | `identity_allowlist` is empty, so `populate_*_provenance` sets `from_agent_verified`/`decided_by_verified` to NULL on every bus row and decision (67k index probes, all misses). The provenance arm of the bus has never asserted anything. | `select count(*) from identity_allowlist;` → 0 |
| 19 | MEDIUM | Planner statistics are missing on 91 of 180 tables (cumulative stats lost at the 2026-04-28 unclean restart; `last_analyze` NULL), including the 479 MB `hadith_embeddings`. Also means `pg_stat_user_tables` "unused" signals are unreliable for this DB. | `select pg_postmaster_start_time(); select count(*) from pg_stat_user_tables where schemaname='public' and n_live_tup=0 and n_tup_ins=0;` → 91 |
| 20 | MEDIUM | pg_cron job 7 (`enforce_challenge_window_timeouts`, every 5 min) re-scans the same 104 production decisions whose challenge window expired (oldest 2026-08-17) and skips each as `audit_required`. Not a cron bug: an unworked audit backlog that the enforcer correctly refuses to auto-accept. | `cron.job_run_details` jobid=7 return messages |
| 21 | LOW | ~20 orchestrator-v0 tables (`chat_history`, `message_queue`, `cto_council*`, `jobs_archive`, `wingmen_brain`, `brain_sync_log`, `qa_findings`…) are referenced only from `legacy/`; 28 tables are empty. Backup and drop with the legacy retirement. | per-table `count(*)` + `grep -rl` |
| — | INFO (clean) | Sequences: 0 of 84 behind `max(id)` (`fleet_health.py sequence_drift()` is doing its job). Triggers: 0 disabled. Tables without a primary key: 0. Dead rows: only `agent_messages` above 1,000 (8.5k, 11.5%, within autovacuum threshold). 41 FKs lack a covering index, none on a hot delete path. | slice C §1, §2, §5, §7 |
