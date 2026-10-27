# Findings A — FUNCTIONS slice (substrate `tscuymavysscrvoberrr`, read-only audit, op#34969)

Audited 2026-10-28 via the read-only runner only. 265 public functions, 34 SECURITY DEFINER.
Key structural facts proven live:
- `postgres` (owner of every app SECDEF function) has `rolbypassrls=true`; no table has FORCE RLS → every SECDEF body runs with RLS fully bypassed.
- Default ACL for postgres-owned public functions = EXECUTE to anon, authenticated, service_role. Several functions additionally carry `=X` (PUBLIC) from `CREATE ... ` under older migrations.
- anon/authenticated have NO table privileges on `agent_messages`, `agent_status`, `identity_allowlist`, `strategic_decisions`, `operator_messages` → the SECDEF RPCs below are the ONLY write path those roles have into the bus, and they are wide open.
- `track_functions=none` → `pg_stat_user_functions` is empty; call counts for these RPCs cannot be proven from the DB.
- `identity_allowlist` has 0 rows → `from_agent_verified` is never set by the provenance trigger; only explicit caller writes set it.

## 1. SECURITY DEFINER inventory (34)

Legend: anon/auth = `has_function_privilege(role, oid, 'EXECUTE')` live result. sp = search_path pinned. "trigger-only" = `RETURNS trigger` — cannot be invoked directly (`trigger functions can only be called as triggers`), so EXECUTE grant is hygiene, not a direct vector.

| Rank | Function | anon | auth | sp | Writes (from body) | Impact |
|---|---|---|---|---|---|---|
| CRITICAL | `post_agent_message(p_from_agent,…,p_priority,p_requires_response,…)` | Y | Y | public | INSERT agent_messages (+ trigger: work_item/work_item_audit via work_item_mirror_bus_trg when requires_response) | anon can forge ANY from_agent/to_agent/priority/requires_response bus row. Zero caller validation. Provenance trigger stamps `posted_by_identity=current_user='postgres'` — identical to 63k legit rows → forgery is forensically indistinguishable. Can wake hub (P1+rr floor), impersonate `orch-console`/`cai`, inject "operator authorization" testimony (CLAUDE.md R1 treats bus rows as attributable). |
| HIGH | `mark_delivered(p_id, p_lease_seconds)` | Y | Y | public | UPDATE agent_messages.delivered_at, lease_expires_at (any id) | anon can mark any bus row delivered / set arbitrary lease → silently suppress delivery to the real recipient (bus DoS / message loss), or steal/extend leases. |
| HIGH | `ack_message(p_id)` | Y | Y | public | UPDATE agent_messages.acked_at (any id) | anon can ack any row → requires_response asks look answered; audit trail falsified. |
| HIGH | `sweep_stale_agents(p_threshold, p_actor, p_base_agent_id)` | Y (PUBLIC `=X`) | Y | public | UPDATE agent_status (status='offline', reaped_by=p_actor, current_task=NULL); fires snapshot_agent_status_to_history | Caller supplies threshold: `'0 seconds'` reaps EVERY non-offline agent in one call; `reaped_by` is free text. Intentionally bypasses `enforce_agent_status_identity` via `set_config('app.current_agent_id', …)`. Fleet-wide status DoS, watchdog/autoscaler confusion. |
| LOW (hygiene) | `trigger_cto_strategist()` trigger-only | Y | Y | public | UPDATE cto_council_sessions; `net.http_post` with Bearer JWT read from `wingmen_config` | Not directly callable. But it reads a secret (`cto_strategist_jwt`) from a plain table; see 2.e. |
| LOW (hygiene) | `sync_org_memberships_to_jwt()` trigger-only | Y | Y | public | UPDATE `auth.users.raw_app_meta_data` | Not directly callable; writes auth schema as postgres. Fires on org_members DML (org_members write path is RLS-scoped). |
| LOW (hygiene) | `handle_new_user()` trigger-only | Y | Y | public | INSERT profiles | Not directly callable. |
| LOW (hygiene) | `populate_agent_messages_provenance()` trigger-only | Y | Y | public,pg_temp | sets NEW.posted_by_identity/from_agent_verified | See §3: stamps `current_user`, which inside any SECDEF caller is `postgres`. |
| LOW (hygiene) | `populate_strategic_decisions_provenance()` trigger-only | Y | Y | public,pg_temp | sets NEW.* | Same design flaw as above for decisions. |
| LOW (hygiene) | `enforce_audit_tier_change_guard()` trigger-only | Y | Y | pg_catalog,public | INSERT decision_tier_changes | Not directly callable. |
| LOW (intended) | `auth_user_org_ids()`, `auth_user_org_ids_with_role(text)`, `auth_user_org_ids_with_roles(text[])` | Y | Y | public | none (STABLE) | RLS helpers used by 27 policies; scoped by `auth.jwt()`/`auth.uid()`; `public.org_members` qualified. OK. |
| LOW | `auth_user_hr_employee_id()` | Y | Y | public | none | Scoped by auth.uid(); reads `hr_employees`/`persons` UNQUALIFIED and anon/authenticated hold TEMP privilege (pg_temp shadowing) — only exploitable with raw SQL, not via PostgREST. Not referenced by any policy. |
| LOW | `get_root_details(text)`, `get_root_frequencies()`, `get_surah_similarity()`, `get_surah_timeline()`, `get_verb_form_stats()`, `resolve_ayah_refs(int[])` | Y | Y | public | none | Read-only Quran corpus tables (word_morphology, ayah_meta) that anon can already SELECT. No escalation. |
| n/a | `admin_mark_offline(text,text)` | N | N | public | UPDATE agent_status | owner fleet_reaper; EXECUTE fleet_reaper+service_role only. Fine. |
| n/a | `agent_messages_cai_gate()`, `strategic_decisions_cai_gate()`, `project_governance_audit_trigger()`, `project_governance_forbid_delete()` | N | N | public | — | postgres+service_role only. Fine. |
| n/a | `audit_board_digest()`, `enforce_challenge_window_timeouts(bool)`, `escalate_full_tier_without_auditor(numeric)`, `escalate_stale_decision_audits()` | N | N | public,pg_temp | strategic_decisions/audit tables | cron-driven, service_role only. Fine. |
| n/a | `get_decision(text)`, `get_repo_context(text)`, `classify_data_provenance(text,text)` | N | N | public / `""` | none | Fine. |
| n/a | `dblink_connect_u(text)`, `dblink_connect_u(text,text)` | N | N | UNPINNED (C) | — | supabase_admin only; C function, search_path irrelevant. Fine. |

search_path: 32/34 pinned; the 2 unpinned are C-language `dblink_connect_u` (supabase_admin-only). No SECDEF pins `pg_temp` LAST except the 4 `public, pg_temp` ones; with `search_path=public` pg_temp is still searched FIRST for relations, and anon/authenticated/service_role all have TEMP on the database (`has_database_privilege(...,'TEMP')=true`). Exploit needs raw SQL (not PostgREST) → LOW, but pin `pg_catalog, public, pg_temp` on rewrite.

## 2. Non-SECDEF functions executable by anon that matter

Census (live): anon can EXECUTE 199 non-SECDEF public functions (119 immutable, 9 stable, 71 volatile, 22 trigger-only) plus all of schema `net`.

a. **`public.dblink*` (incl. `dblink_connect(text)`, `dblink_exec(text)`, `dblink(text,text)`) — PUBLIC EXECUTE (`=X/supabase_admin`)** — HIGH. anon/authenticated can open outbound libpq connections from the DB host (SSRF/port-scan of the VPC, brute-force of local DB passwords, exfil of anything anon can read). PostgREST can call the single-unnamed-`text` overloads (`dblink_connect(text)`, `dblink_exec(text)`, `dblink(text)`) with `Content-Type: text/plain`. Extension installed in `public`, not `extensions`.

b. **`public.http_get(uri)`, `http_post(uri,…)`, `http_put`, `http_delete`, `http(http_request)` (pgsql-http) — anon+authenticated EXECUTE, NAMED params** — HIGH. Directly REST-callable: `POST /rest/v1/rpc/http_get {"uri":"http://169.254.169.254/..."}` → synchronous SSRF from the DB's egress IP, response body returned to the caller.

c. **`net.http_get/http_post/http_delete`, `net.worker_restart`, `net.wake` (pg_net) — anon+authenticated EXECUTE + USAGE on schema `net`** — MEDIUM. Async SSRF; `net` is not a PostgREST-exposed schema (raw SQL needed), and `worker_restart` is a DoS lever on the pg_net worker.

d. **`work_item_mirror(p_kind, p_source_table, p_source_id, d work_item_derived, p_actor)` — invoker rights, writes `work_item` + `work_item_audit`; those tables have RLS OFF and `authenticated` holds INSERT/UPDATE, anon SELECT** — MEDIUM (cross-slice with tables): any authenticated JWT can forge/overwrite work items and audit rows with arbitrary `actor`; anon can read the whole work_item board.

e. **`wingmen_config` / `cto_council` / `cto_council_sessions`** — anon+authenticated hold full DML grants; protected only by RLS policies `auth.role()='service_role'`. `wingmen_config` stores `cto_strategist_jwt` (a bearer secret) as a row. RLS currently holds, but one dropped/misedited policy exposes the secret and lets anon trigger `trigger_cto_strategist` (SECDEF net.http_post with that JWT). MEDIUM, hygiene: secrets belong in vault/env, not a table anon has GRANTs on.

f. `mizan_unlock_retract_gate(uuid)` — invoker, writes `mizan_retract_gate`; anon has UPDATE grant but RLS on with 0 anon policies → denied for anon; authenticated has 1 policy (not evaluated here). LOW.

g. `next_receipt_number(uuid)`, `next_pos_transaction_number(uuid)` — invoker, SELECT MAX from `receipts` (RLS on, 3 policies) / `pos_transactions` (table does not exist → function is broken/dead). LOW. `log_agent_status_identity_violation` is RAISE NOTICE only. LOW.

h. 22 non-SECDEF trigger functions (e.g. `enforce_agent_status_identity`, `work_item_mirror_bus_trg`, `trigger_cai_decision_announce`) carry anon EXECUTE — not directly callable. LOW hygiene; covered by the blanket REVOKE.

i. **Sequences (cross-slice):** 80/84 public sequences are UPDATE-able by anon+authenticated (default ACL `rwU`), including `agent_messages_id_seq`, `strategic_decisions_id_seq`, `operator_messages_id_seq`, `agent_status_history_id_seq`. Raw-SQL only (`setval` is pg_catalog, not REST-exposed) → LOW/MEDIUM; still revoke.

j. **`exec_sql` RPC** is called by `nervous_system/council_agent.py:173` and `nervous_system/council_executor.py:315,394,431` but does NOT exist in any schema (0 rows). Dead path today — but if anyone "fixes" it by creating `exec_sql(query text)` under the current default ACL it becomes anon-callable arbitrary SQL. Flag for deletion of the caller code.

k. No function in any schema contains dynamic `EXECUTE`/`format()` SQL (scan of all 265 bodies). No vault/pg_read_file/COPY references.

## 3. Bus-bypass analysis

- **`post_agent_message`**: a plain `INSERT … VALUES (p_from_agent, p_to_agent, …, p_priority, p_requires_response, …)` under SECURITY DEFINER as `postgres` (bypassrls). It performs NO caller-identity validation of any kind — `p_from_agent` is trusted verbatim; `p_priority` (P0..) and `p_requires_response` are caller-chosen; `p_to_agent='cai'`/`'cc-orchestrator'` allowed. The only trigger that can refuse is `agent_messages_cai_gate` (BEFORE INSERT, only when to_agent='cai' AND message_type in review_request/question/blocker/challenge AND the resolved project has `cai_enabled=false`). `populate_agent_messages_provenance` sets `posted_by_identity := current_user`, which inside this SECDEF function is **`postgres`** regardless of whether the caller was anon, authenticated or the fleet — 62,876 rows already carry `posted_by_identity='postgres', from_agent_verified=NULL`, so a forged row has the exact same provenance fingerprint as the fleet's own posts. `from_agent_verified` is computed from `identity_allowlist`, which is EMPTY (0 rows) → always NULL. Result: with the public anon key anyone can (a) forge testimony from any agent incl. `orch-console`/`cai`/`cc-orchestrator`, (b) post P0/P1 `requires_response` rows that cross the hub wake floor, (c) spam `work_item` via the mirror trigger.
- **`mark_delivered` / `ack_message`**: `UPDATE agent_messages … WHERE id = p_id` with no ownership check (no `to_agent = <caller>` predicate, no lease-holder check). anon can mark others' rows delivered/leased/acked for any id (ids are sequential → trivially enumerable). This is a delivery-suppression and audit-falsification primitive.
- **Delete**: no SECDEF function deletes bus rows and anon/authenticated have no DELETE grant → no delete path for those roles. (Good.)
- **`sweep_stale_agents`**: PUBLIC-executable; `p_threshold` is caller-supplied so `interval '0'` reaps the whole fleet; `p_actor` is free text; it deliberately sets `app.current_agent_id` per row to satisfy `enforce_agent_status_identity`, i.e. the identity guard is bypassed by design for anyone who can call it. Direct agent_status writes are otherwise closed to anon.
- **`agent_messages_cai_gate` / `populate_agent_messages_provenance`**: not independently exploitable (trigger-only), but they do not provide any identity assurance — the "Phase 2" comment in the provenance trigger (switch to `request.jwt.claims ->> 'agent_id'`) was never done, so the bus has no cryptographic/claims-based sender binding at all.

## 4. Proof SQL (all run read-only, outputs trimmed) + remediation (NOT executed)

### F1 — CRITICAL: bus RPCs executable by anon/authenticated
```sql
select p.proname, has_function_privilege('anon',p.oid,'EXECUTE') anon_x,
       has_function_privilege('authenticated',p.oid,'EXECUTE') auth_x, p.proconfig
  from pg_proc p where p.pronamespace='public'::regnamespace and p.prosecdef
   and p.proname in ('post_agent_message','mark_delivered','ack_message','sweep_stale_agents');
```
Output:
```
post_agent_message   True True ['search_path=public']
mark_delivered       True True ['search_path=public']
ack_message          True True ['search_path=public']
sweep_stale_agents   True True ['search_path=public']
```
and direct table path is closed (so these are the only path):
```sql
select r, has_table_privilege(r,'public.agent_messages','INSERT') ins, has_table_privilege(r,'public.agent_messages','UPDATE') upd
  from unnest(array['anon','authenticated']) r;
-- anon False False / authenticated False False
```
Provenance fingerprint collision:
```sql
select posted_by_identity, from_agent_verified, count(*) from agent_messages group by 1,2 order by 3 desc limit 3;
-- postgres  NULL  62876
-- cc-irsyad-coord NULL 1955
-- postgres  true  643
select count(*) from identity_allowlist;   -- 0
```
Remediation:
```sql
REVOKE EXECUTE ON FUNCTION public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.mark_delivered(bigint,integer) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.ack_message(bigint) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.sweep_stale_agents(interval,text,text) FROM PUBLIC, anon, authenticated;
-- then: either drop them (repo grep shows no production caller — only tests/ci_bootstrap_schema.py/audit_mac_mini.py mention them)
-- or rewrite: validate caller (request.jwt.claims role = service_role, or per-agent key -> allowlisted from_agent), add `to_agent = <caller>` predicates to mark_delivered/ack_message, make sweep threshold a server-side constant.
```

### F2 — HIGH: sweep_stale_agents is PUBLIC (`=X`)
```sql
select proname, proacl from pg_proc where proname='sweep_stale_agents';
-- {=X/postgres,postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,service_role=X/postgres}
```
Remediation: as F1 (`REVOKE … FROM PUBLIC` is required in addition to anon/authenticated because of the `=X` entry).

### F3 — HIGH: dblink in public, PUBLIC-executable
```sql
select proname, pg_get_function_identity_arguments(oid) a, has_function_privilege('anon',oid,'EXECUTE')
  from pg_proc where pronamespace='public'::regnamespace and proname in ('dblink','dblink_connect','dblink_exec');
-- dblink(text) True; dblink(text,text) True; dblink_connect(text) True; dblink_exec(text) True ... (all True; dblink_connect_u False)
```
Remediation:
```sql
REVOKE EXECUTE ON FUNCTION public.dblink(text), public.dblink(text,boolean), public.dblink(text,text), public.dblink(text,text,boolean),
  public.dblink_connect(text), public.dblink_connect(text,text), public.dblink_exec(text), public.dblink_exec(text,boolean),
  public.dblink_exec(text,text), public.dblink_exec(text,text,boolean), public.dblink_open(text,text), public.dblink_open(text,text,text),
  public.dblink_open(text,text,boolean), public.dblink_open(text,text,text,boolean), public.dblink_send_query(text,text), public.dblink_fetch(text,integer),
  public.dblink_fetch(text,integer,boolean), public.dblink_fetch(text,text,integer), public.dblink_fetch(text,text,integer,boolean), public.dblink_get_result(text), public.dblink_get_result(text,boolean)
  FROM PUBLIC, anon, authenticated;
-- better: ALTER EXTENSION dblink SET SCHEMA extensions; and revoke there (or DROP EXTENSION if unused — grep repo first).
```

### F4 — HIGH: pgsql-http REST-callable by anon
```sql
select proname, pg_get_function_arguments(oid), has_function_privilege('anon',oid,'EXECUTE')
  from pg_proc where pronamespace='public'::regnamespace and proname in ('http_get','http_post','http_put','http_delete','http');
-- http_get(uri character varying) True; http_post(uri, data jsonb) True; ... all True (named params => /rest/v1/rpc/http_get works)
```
Remediation:
```sql
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;  -- blanket, then re-grant the allowlist below
-- or targeted: REVOKE EXECUTE ON FUNCTION public.http_get(character varying), public.http_get(character varying,jsonb), public.http_post(character varying,jsonb),
--   public.http_post(character varying,character varying,character varying), public.http_put(...), public.http_delete(...), public.http(http_request) FROM PUBLIC, anon, authenticated;
-- move extension to `extensions` schema (not REST-exposed) if it must stay.
```

### F5 — MEDIUM: pg_net exposed to anon (raw SQL only)
```sql
select proname, has_function_privilege('anon',oid,'EXECUTE') from pg_proc where pronamespace='net'::regnamespace;
-- http_get True, http_post True, http_delete True, worker_restart True, wake True, ...
select has_schema_privilege('anon','net','USAGE');  -- True
```
Remediation: `REVOKE USAGE ON SCHEMA net FROM anon, authenticated; REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA net FROM anon, authenticated;`

### F6 — MEDIUM: default ACL keeps re-granting EXECUTE to anon/authenticated for every new function
```sql
select defaclrole::regrole, defaclnamespace::regnamespace, defaclobjtype, defaclacl from pg_default_acl
 where defaclobjtype='f' and defaclnamespace='public'::regnamespace;
-- postgres public f {postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,service_role=X/postgres}
-- supabase_admin public f {... anon=X, authenticated=X ...}
```
This is WHY `mark_delivered`/`ack_message`/`post_agent_message` (created by migration without an explicit REVOKE) are anon-callable; any future RPC will be too.
Remediation:
```sql
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
-- re-grant only what the client apps need (RLS helpers are invoked by the policy engine as the table owner, but PostgREST schema-cache introspection is harmless):
GRANT EXECUTE ON FUNCTION public.auth_user_org_ids(), public.auth_user_org_ids_with_role(text), public.auth_user_org_ids_with_roles(text[]), public.auth_user_hr_employee_id() TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_root_details(text), public.get_root_frequencies(), public.get_surah_similarity(), public.get_surah_timeline(), public.get_verb_form_stats(), public.resolve_ayah_refs(integer[]) TO anon, authenticated;  -- if the Quran app really calls these as anon
-- add a migration-lint: any CREATE FUNCTION in migrations/ must be followed by REVOKE EXECUTE … FROM PUBLIC, anon, authenticated unless whitelisted.
```

### F7 — MEDIUM: work_item / work_item_audit writable by authenticated with RLS OFF (reachable via work_item_mirror and directly)
```sql
select t, has_table_privilege('authenticated','public.'||t,'INSERT') ins, (select relrowsecurity from pg_class where oid=('public.'||t)::regclass) rls
  from unnest(array['work_item','work_item_audit']) t;
-- work_item True False / work_item_audit True False
select has_function_privilege('authenticated','public.work_item_mirror(text,text,bigint,work_item_derived,text)','EXECUTE'); -- True
```
Remediation: `ALTER TABLE public.work_item ENABLE ROW LEVEL SECURITY; ALTER TABLE public.work_item_audit ENABLE ROW LEVEL SECURITY; REVOKE ALL ON public.work_item, public.work_item_audit FROM anon, authenticated; REVOKE EXECUTE ON FUNCTION public.work_item_mirror(text,text,bigint,work_item_derived,text) FROM PUBLIC, anon, authenticated;` (hand to the TABLES slice).

### F8 — MEDIUM: secret stored in `wingmen_config`, a table anon holds full DML grants on (RLS-only protection)
```sql
select relname, relacl from pg_class where relname='wingmen_config';
-- {postgres=arwdDxtm/postgres,anon=arwdxtm/postgres,authenticated=arwdxtm/postgres,service_role=arwdDxtm/postgres}
select policyname, roles, cmd, qual from pg_policies where tablename='wingmen_config';
-- service_role_only_select_config {public} SELECT (auth.role() = 'service_role')
select count(*) from wingmen_config where key='cto_strategist_jwt';  -- (not run; key name visible in trigger_cto_strategist body)
```
Remediation: `REVOKE ALL ON public.wingmen_config, public.cto_council, public.cto_council_sessions FROM anon, authenticated;` and move `cto_strategist_jwt` to Vault (`vault.create_secret`) read via `vault.decrypted_secrets` inside the trigger.

### F9 — LOW: provenance design — `posted_by_identity := current_user` is `postgres` inside any SECDEF path
Proof: body of `populate_agent_messages_provenance` (SECURITY DEFINER) + distribution query in F1 (62,876 rows = 'postgres'). Remediation: in the trigger use `coalesce(current_setting('request.jwt.claims', true)::json->>'role', session_user::text)` and record `session_user` separately; populate `identity_allowlist` (currently empty) so `from_agent_verified` can ever become true via the trigger rather than by caller assertion.

### F10 — LOW: sequences UPDATE-able by anon/authenticated (cross-slice)
```sql
select count(*) filter (where has_sequence_privilege('anon',c.oid,'UPDATE')) anon_upd, count(*) total
  from pg_class c where relkind='S' and relnamespace='public'::regnamespace;  -- 80 of 84 (incl. agent_messages_id_seq, strategic_decisions_id_seq, operator_messages_id_seq)
```
Remediation: `REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated; ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;` (re-grant USAGE only where a client app inserts into a serial table under RLS).

### F11 — LOW: search_path hygiene
```sql
select proname, proconfig from pg_proc where pronamespace='public'::regnamespace and prosecdef and (proconfig is null or not (proconfig::text ~ 'pg_temp'));
-- 30 rows pinned to 'public' (no trailing pg_temp); dblink_connect_u x2 unpinned (C, supabase_admin-only)
select has_database_privilege('anon', current_database(), 'TEMP');  -- true
```
Remediation: on next rewrite of each SECDEF fn `SET search_path = pg_catalog, public, pg_temp`; schema-qualify table refs (`auth_user_hr_employee_id` reads `hr_employees`/`persons` unqualified). Consider `REVOKE TEMP ON DATABASE postgres FROM anon, authenticated` (check PostgREST/supabase tooling first).

### F12 — INFO: `exec_sql` RPC referenced by repo code does not exist
```sql
select count(*) from pg_proc where proname ~* 'exec_sql';  -- 0
```
Callers: `nervous_system/council_agent.py:173`, `nervous_system/council_executor.py:315,394,431`. Delete the callers; never create an `exec_sql` under the current default ACL.

## Priority order for the remediation PR (suggested)
1. F1+F2 (revoke the 4 bus RPCs from PUBLIC/anon/authenticated; they have no production caller in the repo) — one statement, zero fleet impact if confirmed unused; verify with `pg_stat_statements` or by enabling `track_functions=pl` for 24h first.
2. F6 blanket default-ACL fix + `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public` with an explicit re-grant allowlist — this also closes F3/F4 and the 22 trigger-fn grants in one move.
3. F5 (pg_net), F7/F8/F10 to the TABLES/sequences slice owner.
4. F9/F11 as part of the bus-identity (Phase 2 claims-based provenance) rewrite.
