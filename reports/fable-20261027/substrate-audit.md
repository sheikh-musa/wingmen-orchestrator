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

Rows 15–21 (hygiene/residency) are in §4 and rows 22–28 (ledger/drift) in §5; the appendices carry every proving query with live output.

**Verdict in one paragraph.** The substrate is not currently leaking data through the public API: RLS holds on every table that matters and anon reaches no bus or governance row directly. It is, however, one default away from doing so, and the write-side of the bus is already open: with nothing but the project anon key, a caller can post bus rows as any agent, acknowledge or suppress delivery of any row, reap every agent offline, and make outbound HTTP/libpq calls from the database host. Three hardening migrations meant to close exactly these holes were written and are not in effect (117 revoked the wrong grantee, 032 and cai1004 were never applied), and the 040 drop of the reaper was reversed by a hand re-apply. Underneath that, the fleet's own processes all run as `postgres`, so for lanes the bus RPC was never a boundary. The hygiene side has one loud item (a retention cron that has failed 190 times in a row) and one residency item (ihsanos-v0 person and donation rows in the orchestrator store, invisible to the residency sweep). Everything above is read-only proven and the first four remediation steps are zero-impact on first-party code.

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

## 5. Migration ledger vs repo, view/function drift (slice D, full detail in `substrate-audit-D-migration-drift.md`)

How the ledger works (`scripts/apply_migration.py:273`): it stores the full `sha256` of the file bytes; the 12-char prefix is only what the `--gate` bus-row body must contain. ihsanos/goumlyne rows in this ledger are by design (central ledger, `--repo` mandatory for non-orchestrator silos). No evidence of `supabase db push` after decision 962: every later `supabase_migrations.schema_migrations` row was written by an explicit INSERT inside a migration file, with prose summaries rather than CLI SQL.

| # | Sev | Finding | Proof |
|---|-----|---------|-------|
| 22 | HIGH | `sweep_stale_agents(interval,text,text)` was DROPPED by ledgered migration 040 (CAI-766, applied 2026-08-07) and REVOKED from anon by 010, yet is live today as SECURITY DEFINER with anon/authenticated EXECUTE. Cause: bus #31074 (cc-fleet-health, 2026-08-21) re-applied it by direct psycopg after #31068; no migration, no ledger row. This is the same object as finding #3, now with its provenance. | `select oid::regprocedure, prosecdef, has_function_privilege('anon',oid,'EXECUTE') from pg_proc where proname='sweep_stale_agents';` → t, t; `grep -n "DROP FUNCTION" migrations/040_*.sql` |
| 23 | HIGH | The decision-audit governance family has no mainline migration: tables `decision_audits`, `decision_tier_changes`, `decision_tier_escalations`, `audit_board_digest_log`; 13 functions (4 SECDEF incl. `enforce_audit_tier_change_guard`, `audit_board_digest`, `escalate_*`); triggers `trg_audit_tier_change_guard` and `trg_challenge_window_requires_expiry` ON `strategic_decisions`; 7 policies; view `decision_audit_state`; plus a live edit of SECDEF `enforce_challenge_window_timeouts` adding a `decision_audit_required()` skip that no file contains. Only sources are backfill ledger rows pointing at the unmerged branch `nazim/assigned-decision-audit` and `reports/backend-review/cai1293-APPLIED-20260823.sql`. The governance layer that gates decisions is itself unreproducible from `main`. | slice D §4 (`objs_vs_repo.txt`) |
| 24 | HIGH | `boot_briefing` live body matches no file in the tree: three arms removed by the deleted one-off `apply_boot_briefing_diet.py` (PR #88) and one arm (`data_provenance_flag`) added by `scripts/extend_boot_briefing_arm.py` under gate #51717. `schema.sql` is stale. The decision-962 failure mode (CLI re-applying an old body) would silently revert both changes. | `select pg_get_viewdef('boot_briefing'::regclass)` vs latest `CREATE OR REPLACE VIEW boot_briefing` in the tree |
| 25 | HIGH | 108 of 128 `supabase_migrations.schema_migrations` rows have no file under `supabase/migrations/` (CLI history deleted). Roughly 100 live objects (cto_council*, wingmen_config, `populate_agent_messages_provenance` SECDEF, monolith-era ihsanos tables and policies, the ai-scholar corpus) have no reproducible source; 50 live tables have no CREATE TABLE anywhere in the tree, 94/289 policies and 16/37 triggers are named by no migration. | slice D §2, §5 |
| 26 | MEDIUM | 17 migration files have no ledger row: 14 were applied but never ledgered (020–023, 033, 035–037, 041–043, 057, 062: objects verified live), 2 were never applied at all (`024_quality_gate_runs`, `034_coordinator_panes_changed_at`: `to_regclass('quality_gate_runs')` is NULL, `coordinator_panes.changed_at` absent), and 032 (finding #6). | `select migration_name from migration_ledger where silo_ref='tscuymavysscrvoberrr'` vs `ls migrations/` |
| 27 | MEDIUM | 10 ledger rows have no file: 020–023 (a 020→025 renumber in commit 35bbee4 was lost on merge, so the tree still has colliding 020–024), `083_operator_messages_defer.sql` exists in no local branch (gated apply #46373), 4 nazim-branch rows, the hc95 draft. Two ledger shas (`072_operator_asks_tracking`, `086_mamadah_bot_channel`) match no blob in any branch: the applied DDL is unreproducible. 11 further sha mismatches are the PR #92 header edits after apply (benign, but `check_ledger_collision` will now refuse those files). | slice D §1 |
| 28 | LOW | `reel_inbox` (migration 001, ledgered) is gone with no DROP in the repo; `exec_work_items`/`exec_delivery_ledger` live with no ledger row; `vault_access_log` still live beside `exec_broker_access_log`; `share_lane_status_v` has three extra columns from a deleted one-off applier; views `agent_message_stale_claims`, `fleet_drain_freshness`, `cto_council_open_sessions`, `stale_blockers` have no CREATE VIEW anywhere. Numbering gaps 002, 025–029, 081, 083, 122–124 (reserved by PR #490) and in-tree duplicates 037, 043, 072. | slice D §1, §5 |
| — | INFO (clean) | All bus and cai-gate functions match their latest migration text: `post_agent_message`, `mark_delivered`, `ack_message` (117), `agent_messages_cai_gate`, `strategic_decisions_cai_gate` (064), `classify_data_provenance`, `admin_mark_offline`, `project_governance_*`, `get_decision`, `get_repo_context`, `populate_strategic_decisions_provenance`. Twelve views differ from their migration by formatting only. | slice D §3 |

The thread through §5: **hand-applied objects are the norm, not the exception**, and three of the top-ten security findings (#2, #3, #4) are the direct result: a lockdown that lived only in a migration file was undone by a later hand-applied `CREATE OR REPLACE`, or never applied at all. Finding #22 is the cleanest example: a DROP with a CAI ref and a ledger row, reversed two weeks later by a bus-row-only re-apply.

## 6. Recommended remediation sequence (none of this was executed; every step goes through the gated applier, never `supabase db push`)

1. **One migration, four REVOKEs, zero callers (closes #1–#3):** `REVOKE EXECUTE ON FUNCTION post_agent_message(...), mark_delivered(bigint,integer), ack_message(bigint), sweep_stale_agents(interval,text,text) FROM PUBLIC, anon, authenticated; GRANT EXECUTE ... TO service_role;`. Post-apply assert in the same file: `select count(*) from pg_proc p where proname in (...) and has_function_privilege('anon',p.oid,'EXECUTE')` must be 0. Repo grep shows no first-party caller, so this is a zero-impact change.
2. **Fix the default ACL so it cannot recur (closes the root cause behind #2, #4, #6, #12):** `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated; … REVOKE ALL ON TABLES FROM anon, authenticated; … REVOKE ALL ON SEQUENCES FROM anon, authenticated;` and the same three for `FOR ROLE supabase_admin`. Then a one-time bulk `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated` followed by an explicit allowlist re-grant (Quran-corpus readers `get_root_*`, `resolve_ayah_refs`, `get_surah_*`, `get_verb_form_stats`; RLS helpers `auth_user_org_ids*`; `ui_events` ingest). Note the 117 header's concern ("would kill this migration's own SECDEF functions since they share an owner"): a REVOKE from anon/authenticated does not touch the owner's implicit rights, so that objection does not apply here.
3. **Apply the two already-written hardening migrations** (`032_anon_write_revoke_nontelemetry.sql`, `infra/cai1004_revoke_pg_net_public_anon_authenticated.sql`) after re-reading them against today's table list; they predate ~30 tables. Closes #4 (net.*) and #6. Move `http` and `dblink` extensions to the `extensions` schema or revoke their EXECUTE from anon (part of step 2's bulk revoke, but the extension move is the durable fix because `CREATE EXTENSION` re-runs would re-grant).
4. **`work_item` / `work_item_audit`:** `ENABLE ROW LEVEL SECURITY`, `REVOKE ALL FROM anon, authenticated`, add `_console_ro` policies (#5). Also `REVOKE UPDATE ON exec_broker_access_log FROM exec_broker_role` (append-only log).
5. **Deny-all mismatches (#9):** add `agent_messages_exec_broker_ro` scoped to the broker's gate ids and commit the hc95 GRANT as a numbered migration; decide `exec_runner` on `strategic_decisions` (policy or drop); GRANT `tablet_onboarding_telemetry` to `console_readonly`.
6. **Cron job 6 (#15):** `select cron.alter_job(6, command => $$DELETE FROM agent_status_history WHERE recorded_at < now() - interval '90 days'$$)`; first run in batches of 50k; `VACUUM (ANALYZE) agent_status_history`. Add a `cron.job_run_details` failure-rate check to `fleet_health.py` so a 190/190 failure cannot stay silent for six months.
7. **`ANALYZE;`** once (#19), then confirm autovacuum picks up the 91 tables.
8. **Residency (#16, #17):** route to cc-ihsanos-coord to classify the ihsanos-v0 rows seed-vs-real from the seed scripts; if real, encrypted export to the designated silo then gated DROP of the 21 tables plus `auth.users`; either way extend `scripts/residency_sweep.py` with a table-existence check on this ref. Separately, record ai-scholar's store designation in `docs/data-store-registry.md` (ratify or plan the move).
9. **Secrets and ghosts (#8, #11):** rotate `cto_strategist_jwt`, move it to `vault.secrets`, drop `trigger_cto_strategist` + the `cto_council*` tables with the legacy retirement; delete the `exec_sql` call sites in `nervous_system/council_*.py` or add a test asserting the function must not exist.
10. **Views (#13):** `ALTER VIEW … SET (security_invoker = true)` on the seven definer views; revoke the dead anon grants on `cto_council_open_sessions`, `stale_blockers`.
11. **Make this audit a 10-minute invariant:** the proving queries in §2–§5 are all read-only and run under `console_readonly`; add them to `invariant_assertion_runs` via cc-fleet-health's sweep and a post-apply hook in `scripts/apply_migration.py` that diffs anon/authenticated-reachable objects before vs after each migration ("born-open" check). See NOVEL IDEAS #2.

## 7. Reproducibility

- Runner used for every query: a 15-line psycopg script connecting with `options="-c default_transaction_read_only=on"` and `read_only=True`, rolling back after each statement. A `CREATE TABLE` probe was rejected before the first audit query.
- Catalog snapshots (functions with bodies, grants, policies, relations, sequences, stats, views, triggers, default ACLs, ledger, cron) were dumped at 19:14Z and are regenerable from the queries at the top of each appendix.
- Each appendix carries the exact read-only SQL and its trimmed live output for every finding. Counts in this summary were re-run by cc-substrate-2 at 19:17–19:38Z where they drive a rank.
- Not done, by scope: no ihsanos multi-tenant DB, no client silo, no ADCDA prod, no rows from any person/financial table, no DDL/DML, no merges or deploys.

## NOVEL IDEAS (Musa op#34981-82) — from the substrate auditor's vantage point

**1. Make the database the identity layer: one Postgres role per fleet body, no shared `postgres` DSN.**
Today every body is `postgres` (finding #7), so RLS, the bus RPC and `posted_by_identity` are theatre for lanes; fleet doctrine is enforced by hooks and promises. If each body (hub, console, SRE, each autoscaler lane) connected as its own NOLOGIN-inherited role minted by the autoscaler with a 24h password, then `current_user` IS the agent id, RLS becomes real for first-party code, a lane physically cannot write `strategic_decisions` or `to_agent='cc-orchestrator'` rows, and the five singleton pens become GRANTs rather than lease checks.
- *Why feasible for us:* the policies already exist (289, mostly role-keyed), every DB access already goes through `scripts/lib` helpers that read one env var, and `switch_lane_token` already rotates per-lane credentials; this is the same pattern applied to the DSN.
- *First step:* mint `lane_cc_substrate` (INSERT on `agent_messages` only via `post_agent_message`, SELECT on its own inbox), point one lane's `DATABASE_URL` at it, and watch what breaks for a day.
- *Unlocks:* forge-proof bus provenance, the autoscaler can hand out and revoke DB identity with the token, and most of CLAUDE.md's "never do X" paragraphs become `permission denied`.

**2. Turn this audit into a 10-minute invariant, not a night: "born-open" linting inside `apply_migration.py` + the proving queries as `invariant_assertion_runs`.**
Three hardening migrations in this report were written and never effective (032 never applied, cai1004 never applied, 117's REVOKE a no-op). The fleet already runs `invariant_assertion_runs` and `fleet_health.py sequence_drift()`; the 14 proving queries in this report are the lint. Add a pre/post diff in the gated applier: "which objects are executable/writable by anon or authenticated AFTER this migration that were not BEFORE" and refuse to apply unless the migration names them.
- *Why feasible:* every query here is read-only SQL that already runs under the console_readonly DSN; `apply_migration.py` already wraps each apply in one psycopg transaction, so the diff is two catalog snapshots.
- *First step:* commit `scripts/substrate_lint.py` with the queries from §2–§4, wire it into cc-fleet-health's 10-minute sweep, page on any new row.
- *Unlocks:* security posture becomes a console tile with a timestamp; "Fable may refuse pure security work" stops mattering because the work is a cron job.

**3. Sell the substrate: package the fleet nervous system (bus, asks ledger, governance floors, leases, invariants) as a product for other agent-run agencies.**
The repo is already one-repo-zero-forks with residency doctrine forcing tenant separation; the hard parts other teams lack (durable operator log, at-least-once reconciliation, singleton leases, cai-advisory governance, hash-chained audit) exist and are battle-scarred. The audit also shows the cost of not productising: 20+ hand-applied objects, 3 ineffective migrations, foreign-repo rows in the ledger.
- *Why feasible:* `migrations/` + `schema.sql` + `nervous_system/` are the install; the first external tenant can be ourselves (a second Wingmen project on a fresh Supabase ref), which doubles as the drift test this report could not run.
- *First step:* `scripts/bootstrap_substrate.py` that stands up a blank ref from `migrations/` alone and runs the lint from idea 2; count what is missing (that count is the drift report).
- *Unlocks:* a second revenue line that is not per-client bespoke work, plus a forcing function that drives hand-applied drift to zero.

**4. Self-describing objects: stamp every live view, function and policy with its migration sha via `COMMENT ON`, so drift is a catalog join, not a text diff.**
Decision-962 class (a CLI silently stripping view arms) and the 20+ migration-less objects found tonight are only detectable by diffing migration text against `pg_get_viewdef`, which is slow and fuzzy. If `apply_migration.py` adds `COMMENT ON <object> IS 'mig:<12-char sha>'` for every object the migration touches, then `pg_description` vs `migration_ledger` is one query, and any object without a comment is hand-applied by definition.
- *Why feasible:* the applier already computes the 12-char sha and already parses the file; `COMMENT ON` is transactional and free.
- *First step:* backfill comments for the 19 views and 34 SECDEF functions from the ledger, then make the lint from idea 2 flag uncommented objects.
- *Unlocks:* `boot_briefing` arm-stripping detected in seconds; reviewers can see provenance in the Supabase UI.

**5. Scheduled pool burn-down: let the autoscaler spend the last 90 minutes of every token-pool window on a pre-authorised backlog of read-only work.**
Tonight's "use the pool before it resets at 21:00Z" was a manual operator instruction. The pool reset time is known; the fleet has a long queue of read-only, lossless-if-interrupted work (audits like this one, test sweeps, view/function drift checks, idea rounds, doc refreshes).
- *Why feasible:* `priority_sla_watchdog`, the autoscaler and the lease/lane registry already exist; a burn-down is one more lane spin-up trigger with a fixed task list and a hard stop at reset.
- *First step:* a `pool_burndown` bus row posted 90 minutes before each reset with 3 read-only task templates; measure how many sessions it consumes vs. what would have expired unused.
- *Unlocks:* continuous audits and refactors on compute that would otherwise evaporate, with zero risk because every task is read-only and branch-only.
