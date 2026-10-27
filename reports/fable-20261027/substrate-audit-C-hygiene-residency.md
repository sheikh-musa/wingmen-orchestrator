# Substrate audit — slice C: DATA HYGIENE + RESIDENCY (subagent report, verbatim; headline items re-verified live by cc-substrate-2 at 19:30Z)

Target `tscuymavysscrvoberrr`, PostgreSQL 17.6, read-only via the session-level read-only runner, 2026-10-27 19:16–19:25Z. No rows from person/financial tables were read or printed — counts, column names, timestamps only. Supporting data (sequence comparison, per-table counts) was generated in the lane scratchpad and is reproducible from the queries below.

## 0. Caveat that shapes everything: pg_stat counters are not trustworthy here

91 of 180 tables show `n_live_tup=0, n_tup_ins=0, last_analyze=NULL` yet hold rows (`ayat` 6,236; `hadiths` 36,095; `word_morphology` 77,429; `persons` 58). Postmaster started 2026-04-28 08:20Z, `stats_reset` NULL — PG17 drops cumulative stats on unclean shutdown, so anything last written before 04-28 looks "never used".
```sql
select pg_postmaster_start_time(), (select count(*) from pg_stat_user_tables where schemaname='public' and n_live_tup=0 and n_tup_ins=0);
-- 2026-04-28 08:20:28Z | 91
select 'ayat',count(*) from ayat union all select 'persons',count(*) from persons;  -- 6236 | 58
```
Sections 2–3 therefore use real `count(*)` (generated UNION over all 180 tables). Also: those 91 tables have **no planner statistics** (incl. `hadith_embeddings` 479 MB, `identity_allowlist` 67k scans, `ayat` 1.55M scans). **P2 — remediation: one-off `ANALYZE;`.**

## 1. Sequences vs max(id) — CLEAN

Generator over `pg_attribute` + `pg_get_serial_sequence()` + `attidentity in ('a','d')` → 84 columns (75 serial, 9 identity) → UNION ALL of `max(col)` → joined to `pg_sequence_last_value(seq)`.
Result: **0 drifted, 75 ok, 9 never-called (empty tables, max NULL — not drift)**. `held_commitments` 735=735, `operator_backlog` 128=128, `agent_messages` 78620=78620, `agent_status_history` 699249=699249. `fleet_health.py sequence_drift()` is working. INFO.

## 2. Dead rows / bloat

Only `agent_messages` exceeds 1000 dead (8,459 / 11.5%, autovac 2026-10-20, 136 MB) — within the 20% scale factor. The other 45 hits (cc_cai_audit_log 7 live/95 dead, fleet_lanes 55/58, orch_lease 1/24, pool_usage 3/27, etc.) are tiny hot config/lease tables under the 50-row threshold. P3, no action.

Top sizes: hadith_embeddings **479 MB** (281 MB idx + ~190 MB TOAST), tafsir_embeddings **299 MB**, agent_status_history **190 MB** (699k rows), agent_messages 136 MB, hadiths 83 MB, juridical_embeddings 57 MB, tafsir_entries 47 MB, operator_messages 27 MB, asbab_embeddings 15 MB, strategic_decisions 13 MB, word_morphology 12 MB, ayat 8 MB. **~1.0 GB (55% of the DB) is the ai-scholar corpus, not fleet substrate.**

## 3. Unused tables (by real count, not stats)

**Empty (count=0), 28:** fleet — agent_status_identity_violations, anchor_batches, anchor_queue, blocking_tasks, cc_session_messages, data_exports, exec_delivery_ledger, exec_work_items, fleet_lessons, fleet_stall_state, identity_allowlist, operator_ask_turnaround, provisions, repo_events, shared_feed_dedup; ai-scholar/mizan — ayah_themes, mamadah_notes, mamadah_sources, mizan_auto_scores, mizan_eval_runs, mizan_judge_shadow, mizan_user_feedback; ihsanos-v0 — field_events, payments, pending_signups, person_relationships; other — audit_portal_config, revenue_ledger.

**`identity_allowlist` is EMPTY** but both provenance triggers (`populate_agent_messages_provenance`, `populate_strategic_decisions_provenance`) consult it — so `from_agent_verified` / `decided_by_verified` is NULL on every bus row and decision; its 67,431 index scans are all misses. **P2 (governance gap).**

**Cold (rows but nothing written since 04-28, last created_at ≤ 07-13):**
- ihsanos-v0 product: persons 58, profiles 21, org_members 23, org_role_permissions 112, organizations 6, organizations_fiscal_config 6, person_roles 59, donations 31, donation_categories 26, receipts 31, platform_admins 2, accounts 9, sites 5, site_links 9, site_templates 3, site_content 7, onboarding_events 39, change_requests 3, claim_tokens 30, user_unlocks 4, topics 23.
- legacy orchestrator v0 (referenced only from `legacy/cto_bot.py`, `legacy/provisioner.py`, `legacy/wingmen_orch.py`): chat_history 157, usage_log 144, message_queue 78, cto_council 61, cto_council_sessions 9, jobs_archive 131, client_repos 1, wingmen_config 3, ui_events 1,635, audit_log 191, cc_work_sessions 167, bug_pipeline_readiness 10, deploy_status 10, strategic_decisions_archive 12, qa_findings 1,291, repo_memory 22, repo_snapshot 9, drift_audits 60, wingmen_brain 222, brain_sync_log 809. P3 — backup + drop after legacy retirement.
- ai-scholar corpus: ayat, ayah_meta, ayat_topics, hadiths, hadith_collections, hadith_embeddings, tafsir_entries, tafsir_embeddings, asbab_nuzul, asbab_embeddings, mutashabihat, word_morphology, mizan_eval_set, mizan_human_reviews, mizan_retract_gate, daily_attestations, audit_key_registry, review_dimensions. `hadiths`, `juridical_texts`, `mizan_interactions`, `word_morphology` have zero code references in this repo. `scripts/daily_backup.sh` dumps `ayat` and `donations` from the substrate.

## 4. RESIDENCY — the substantive findings

Origin: `supabase_migrations.schema_migrations` contains `tadabbur_data`, `seed_word_morphology_*`, `create_al_bayan_knowledge_schema`, `create_hadith_tables`, `hr_module`, `012_storefront_ecommerce`, `013_invoicing_module`, `015_school_module`, `022_qurban_module`, `white_label_bot_system`, `cto_council` — this DB was the shared early dev DB for ihsanos + ai-scholar + orchestrator v0. HR/school/qurban/storefront tables were dropped; the rest remains.

**4.1 Person/financial data in the orchestrator substrate — P1 if real, P2 if seed.**
```sql
select 'persons',count(*),count(nric_encrypted),count(email),count(phone),count(date_of_birth) from persons
union all select 'accounts',count(*),0,count(email),0,0 from accounts;  select count(*) from auth.users;
```
persons 58 (email ×41, phone ×29, nric 0, dob 0; created 04-04→04-14; 2 org_ids) · profiles 21 (phone ×1) · accounts 9 (email ×8, tg_chat_id; 07-12) · organizations 6 (uen ×1) · org_members 23 · donations 31 (amount, payment_method, reference_no; 04-04→04-06; 1 org) · receipts 31 (receipt_number, amount, pdf_path) · chat_history 157 (free-text content, 03-30→04-13) · **auth.users 21**. None carry the irsyad org id (0 rows in persons/donations/receipts/organizations/org_members) — this is the ihsanos-v0 April-2026 tenant plus the July-12 white-label TG onboarding batch, **not** the CAI-RESP-369 leftover. Seed-vs-real cannot be decided without reading rows (forbidden here). **Enforcement gap:** `scripts/residency_sweep.py` checks only `ceayjeamtmcyzzvqflus` for irsyad's org_id; the orchestrator substrate is in no client's `foreign` list and the sweep is org_id-keyed, so this data is invisible to the standing gate.
Remediation: cc-ihsanos classifies seed vs real from seed scripts; gated purge/DROP of the 21 ihsanos-v0 tables + auth.users after an encrypted backup to the designated silo; add tscuy with a **table-existence** check to `residency_sweep.py`.

**4.2 ai-scholar corpus and live interaction data — P2.** ~1 GB corpus plus still-live `mizan_interactions` (280 rows, created_at to **2026-10-23**), `ruling_audit_log` (296, to 10-23), `juridical_embeddings` (1,880, to 10-01). Migration ledger confirms a non-orchestrator repo applying here:
```sql
select repo,migration_name,applied_at::date,note from migration_ledger where silo_ref='tscuymavysscrvoberrr' and repo<>'orchestrator';
-- ai-scholar | 20261002_001_madhab_label_comparative.sql | 2026-10-01 | gate=48482 from=orch-console
-- wingmen-orchestrator | hc95_exec_broker_access_log | 2026-10-20   (repo-name variant, benign)
```
ai-scholar's designated store is the orchestrator substrate by default, not by decision. Remediation: ratify or decline an explicit designation in `docs/data-store-registry.md`; if declined, plan a silo move (embeddings are 55% of the DB).

**4.3 Secret in a plain table — P2 (values not read).** `wingmen_config` keys: `cto_strategist_url`, `cto_strategist_jwt`, `pipeline_clock_last_run`; `trigger_cto_strategist` (SECURITY DEFINER on `cto_council` INSERT) reads the JWT and `net.http_post`s with it. Legacy, cold since April. Remediation: rotate JWT, move to `vault_secrets`, drop trigger + council tables.

**4.4** `sync_org_memberships_to_jwt` (SECURITY DEFINER on `org_members`) rewrites `auth.users.raw_app_meta_data` — ihsanos-v0 claims sync; goes with the 4.1 purge. P3.

## 5. Triggers (37)

0 disabled (all `tgenabled='O'`). 9 SECURITY DEFINER trigger functions, all with pinned `search_path`: agent_messages_cai_gate, populate_agent_messages_provenance, strategic_decisions_cai_gate, populate_strategic_decisions_provenance, enforce_audit_tier_change_guard, trigger_cto_strategist (4.3), sync_org_memberships_to_jwt (4.4), project_governance_audit_trigger, project_governance_forbid_delete.
Bus/governance: `trg_agent_messages_cai_gate` rejects routing to cai when the sender's project has `cai_enabled=false` (op#20708); `trg_agent_messages_provenance` stamps `posted_by_identity=current_user` and `from_agent_verified` via the (empty) allowlist; `work_item_mirror_bus_trg` / `work_item_mirror_commitment_trg` mirror into `work_item` with `EXCEPTION WHEN OTHERS → WARNING` (never block, but silently fail); `trg_strategic_decisions_cai_gate` refuses `decided_by='cai'` when every touched project has cai off; `trg_challenge_window_requires_expiry` forces `challengeable_until`; `trg_audit_tier_change_guard` (CAI-1009) forbids dropping FULL tier while closeable and logs to `decision_tier_changes`; `cai_decision_announce_*` auto-posts the review bus row; `cai_decision_autoclose_announce` auto-responds it on implementation. INFO.

## 6. pg_cron (6 jobs)

| job | what | 7d failed/runs | all-time |
|---|---|---|---|
| 5 governance_banned_prefix_purge_24h | DELETE agent_messages, subject `^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):` AND unread AND not forwarded AND not skipped AND >24h | 0/7 | 2/190 (04-28 startup timeouts) |
| **6 agent_status_history_90d_ttl** | DELETE agent_status_history WHERE created_at < 90d | **7/7** | **190/190** |
| 7 notifier-fix-enforcer */5 | enforce_challenge_window_timeouts() | 0/2016 | 127/53,979 (all 04-28) |
| 10 escalate-stale-decision-audits hourly | | 0/168 | 0/1726 |
| 11 audit-board-digest | | 0/7 | 0/72 |
| 15 tablet_onboarding_telemetry_30d_ttl | DELETE >30d (2-row table) | 0/7 | 0/16 |

**6.1 Job 6 has failed every run since 2026-04-21 — P1 (hygiene).** `ERROR: column "created_at" does not exist` — the column is **`recorded_at`**. Never deleted a row:
```sql
select jobid, count(*) runs, count(*) filter (where status='failed') failed from cron.job_run_details where jobid=6 group by 1;  -- 6 | 190 | 190
select count(*), count(*) filter (where recorded_at < now()-interval '90 days'), pg_size_pretty(pg_total_relation_size('agent_status_history')) from agent_status_history;
-- 699032 | 176709 (25.3%) | 190 MB      (re-verified 19:30Z)
```
Unbounded growth. Remediation: `cron.alter_job(6, command => $$DELETE FROM agent_status_history WHERE recorded_at < now() - interval '90 days'$$)`, batched first delete, VACUUM.

**6.2 Job 7 re-scans the same 104 decisions every 5 min — P2 (governance backlog, not a cron bug).** Mode `write_mode`; all 104 prod rows in `challenge_window` with expired `challengeable_until` (oldest 2026-08-17) hit `skipped_audit_required` (`decision_audit_required()` true for 104/104). The enforcer correctly refuses to auto-accept; job 10 returns "0 rows" hourly. Owner: governance slice.

## 7. Orphans

- **No-PK tables: 0/180.** INFO.
- **41 FKs without covering index.** Fleet ones: `agent_status(blocked_on_msg_id, blocked_on_decision_ref, base_agent_id)`, `strategic_decisions(superseded_by, unblocks_task_id)`, `fleet_lanes(base_agent_id)`, `tg_out(channel_key)`, `orch_lease`/`fleet_health_lease(holder)`, `build_log(job_id)`, `bug_reports(job_id, qa_finding_id)`, `jobs(client_id)`; ~25 more on cold ihsanos-v0 tables. Cost only on DELETE of referenced rows: job 5 deletes agent_messages → seq scan of 32-row agent_status. P3, none now.
- **RLS on + zero policies (17):** accounts, asbab_embeddings, audit_chain_boundaries, change_requests, claim_tokens, console_memory_backup, finance_subscriptions, fleet_proposals, hadith_embeddings, onboarding_events, opportunities, orch_lease, revenue_ledger, site_links, sites, tafsir_embeddings, user_unlocks. Deny-by-default for non-owner roles; intended for the fleet ones. INFO.
- **RLS OFF (6):** `exec_broker_access_log, invariant_assertion_runs, share_lane_labels, share_pool_map, work_item, work_item_audit` — see slice B (work_item exposed to anon/authenticated).

## Summary

| # | finding | sev | remediation (not executed) |
|---|---|---|---|
| 6.1 | agent_status_history TTL cron failed 190/190 (`created_at`≠`recorded_at`); 699k rows/190 MB, 25% past TTL | **P1** | alter_job to `recorded_at`, batched delete, VACUUM |
| 4.1 | ihsanos-v0 person/financial tables in substrate: persons 58 (41 emails/29 phones), donations 31, receipts 31, accounts 9, auth.users 21, chat_history 157; not covered by residency_sweep | **P1 if real / P2 if seed** | cc-ihsanos classifies; gated purge/DROP; add tscuy + table-existence check to sweep |
| 4.2 | ai-scholar corpus ~1 GB (55% of DB) + live mizan writes to 10-23 + ai-scholar migrations applied here; no designation on record | P2 | ratify/decline designation in data-store-registry; silo move if declined |
| 0/2.3 | stats lost at 04-28 restart; 91 tables with no planner stats | P2 | `ANALYZE;` |
| 3.1 | `identity_allowlist` empty → `*_verified` NULL on every bus row/decision | P2 | populate or retire (governance) |
| 6.2 | 104 expired-unaudited prod decisions re-scanned every 5 min since 08-17 | P2 | audit backlog (governance) |
| 4.3 | `cto_strategist_jwt` in `wingmen_config`, used by legacy SECURITY DEFINER http_post trigger | P2 | rotate, vault, drop |
| 7.3 | RLS OFF on work_item, work_item_audit, exec_broker_access_log, invariant_assertion_runs, share_* | P2 | → slice B |
| 3.2 | ~20 legacy-orch-v0 tables referenced only from `legacy/` | P3 | backup + drop |
| 2 | dead rows: only agent_messages >1000 (8.5k, 11.5%) | P3 | none |
| 7.2 | 41 unindexed FKs, none on hot delete paths | P3 | none |
| 1/5/7.1 | sequences 0 drifted; 0 disabled triggers; 0 PK-less tables | INFO | none |
