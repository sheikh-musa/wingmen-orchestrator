# Audit slice D — migration ledger vs repo, view/function drift (substrate `tscuymavysscrvoberrr`)

Read-only. Every DB fact came through the `ro` runner (default_transaction_read_only=on). Repo = worktree `/Users/sheikhmusa/wingmen/orchestrator.wt-fable-2` at HEAD `7f10ed1` (not modified). Dumps in `scratchpad/dump/`. Supporting outputs: `scratchpad/views_drift.txt`, `scratchpad/fn_drift.txt`, `scratchpad/objs_vs_repo.txt` (full function/trigger/policy/table cross-check).

How the ledger sha is computed (`scripts/apply_migration.py:273`): `hashlib.sha256(path.read_bytes()).hexdigest()` — full 64-hex sha of the file bytes, stored whole in `migration_ledger.sha256`. The 12-char prefix (`_GATE_SHA_PREFIX_LEN = 12`, line 250) is only what the `--gate` bus row body must contain; the ledger row is full-sha. Central ledger IS by design: apply_migration.py writes `migration_ledger` on the orchestrator substrate for any `--silo` in PRODUCTION_SILOS (ceayj/goumlyne included), keyed `(repo, migration_name, silo_ref)`; `--repo` is mandatory for non-orchestrator silos (bus #44358). ihsanos/goumlyne rows in this ledger are therefore expected, not a finding.

---

## 1. Ledger vs files (silo tscuy, repo orchestrator/wingmen-orchestrator — 110 ledger rows, 117 files)

### 1(a) Files in migrations/ with NO ledger row — 17
Proof: python sha/name join of migrations/*.sql vs dump/migration_ledger.tsv; then one RO union query (to_regclass / information_schema.columns / pg_proc / pg_policies) per declared object.

| file | objects live? | verdict |
|---|---|---|
| 020_operator_messages_sender_identity | from_user_id/from_username/from_name = true | applied, UNLEDGERED |
| 021_operator_messages_cos_triage | cos_triage = true | applied, UNLEDGERED |
| 022_opportunities_ledger | table opportunities + fn = true | applied, UNLEDGERED |
| 023_fleet_health_lease | table = true | applied, UNLEDGERED |
| 024_quality_gate_runs | table = FALSE | NEVER APPLIED |
| 030_rls_lockdown_3_tables | 5 policies = true; self-inserted into supabase_migrations as 20260722000000 | applied; only in CLI table |
| 032_anon_write_revoke_nontelemetry | declares CLI version 20260722020000 -> row ABSENT (`select version,name from supabase_migrations.schema_migrations where version='20260722020000'` -> 0 rows) | status UNKNOWN (REVOKE-only, no ledger anywhere) |
| 033_agent_status_auth_attribution | agent_status.host = true | applied, UNLEDGERED |
| 034_coordinator_panes_changed_at | column FALSE, fn FALSE | NEVER APPLIED |
| 035_operator_backlog | table + 2 policies = true | applied, UNLEDGERED |
| 036_pool_usage | table + policies = true | applied, UNLEDGERED |
| 037_pool_pace | pool_usage_history + pool_usage.pace = true | applied, UNLEDGERED |
| 041_body_activity_verdict | table + policies = true | applied, UNLEDGERED |
| 042_pane_context | table + policies = true | applied, UNLEDGERED |
| 043_pane_context_pct | pane_context.pct = true | applied, UNLEDGERED |
| 057_pane_context_pane_k_at | pane_context.pane_k_at = true | applied, UNLEDGERED |
| 062_fleet_lane_autoscale_log | table + deny_all policy = true | applied, UNLEDGERED |

Severity MEDIUM (14 applied-unledgered — the exact shape apply_migration.py exists to prevent); 024/034 dead files LOW.
Remediation: backfill 14 rows via the CAI-RESP-1054 backfill pattern; delete or apply 024/034 explicitly; resolve 032 by checking its REVOKEs against table_grants.tsv and ledger or drop it.

### 1(b) Ledger rows with no matching file (name OR sha) — 10
| ledger row | where it lives |
|---|---|
| 020_shared_feed_dedup.sql | branch origin/feat/war-room-feed (5cd26a0). Table shared_feed_dedup + policy deny_all_shared_feed_dedup live. |
| 021_invariant_registry / 022_schema_drift_findings / 023_migration_ledger | branch origin/feat/preventative-gates. Commit 35bbee4 (07-22) renamed THIS branch's 020-024 -> 025-029 to dodge the collision but the tree still has 020-024 and no 025-029: the rename was lost on merge. Tables migration_ledger + schema_drift_findings live. |
| 083_operator_messages_defer.sql (apply_migration.py 09-30, gate=46373, sha 0812c26db48c) | In NO local branch (`git log --all -- 'migrations/083_*'` shows only 083_operator_asks_surface, renamed to 084 in 671fe28). Also in supabase_migrations as version '083' with EMPTY statements. Applied from an unpushed worktree. |
| hc95_exec_broker_access_log | migrations/drafts/hc95_exec_broker_access_log.sql sha 2692d73bf977 != ledger 4180a6927cfe (note says applied version added REVOKEs). Expected. |
| nazim/assigned-decision-audit:052/053/055/057 | branch not in tree; see section 4. |

Severity HIGH for 083 (gated apply with no retrievable source) and the nazim family; MEDIUM for 020-023.
Remediation: recover 083 from the applying worktree (gate #46373 body carries sha12) and commit it; port 020-023 from feat/* as 025-029; port nazim 052-057 into migrations/ under new numbers with an already-applied ledger note.

### 1(c) sha256 mismatch (name matched) — 13
Proof: `shasum -a 256 migrations/009_ghost_reaper.sql` -> 47f99f596602… vs ledger 44f8edbbd78a…; `git show <commit>:migrations/<f> | shasum -a 256` across history.
- 11 explained, comment-only edit after apply: 009, 017, 018, 031, 040, 044, 045, 046, 047, 048, 056 — ledger sha == blob before commit 6d1ed56 (2026-09-07, PR #92) which added the "-- Apply via scripts/apply_migration.py … NEVER supabase db push" header (`git diff 6d1ed56^ 6d1ed56 -- migrations/009_ghost_reaper.sql` = 2 comment lines). Benign behaviourally, but apply_migration.py check_ledger_collision will now refuse these files ("already ledgered … but the file on disk differs").
- 2 unexplained, ledger sha matches NO blob in any branch: 072_operator_asks_tracking.sql (ledger 95cafd59d5e2 / file 5892d5412666; its commit says "ledgered live-apply incident") and 086_mamadah_bot_channel.sql (ledger d8cacfd3cfb4 / file f1dedacef407). Applied DDL not reproducible from git.
Severity MEDIUM (11) / HIGH-ish (072, 086).
Remediation: ledgered note amendment for the 11 (never a hand UPDATE); for 072/086 diff live objects vs file and commit what matches live; add a pre-commit guard refusing edits to an already-ledgered migrations/*.sql.

### 1(d) Numbering
Gaps: 002, 025-029, 081, 083, 122-124 (122-124 reserved by open PR #490 — commit d1941bc). Duplicates in tree: 037 (admin_mark_offline / pool_pace), 043 (agents_single_owner / pane_context_pct), 072 (oeh_bot_channel / operator_asks_tracking); plus ledger-documented 052-057 double use across branches — `apply_migration.py <NNN>` is ambiguous for these. drafts/invariant_assertion_runs.sql and drafts/invariant_registry_state_cai1028.sql are byte-identical to ledgered 049/050 (dead dupes, LOW).

---

## 2. supabase_migrations.schema_migrations vs migration_ledger
Columns: version text, statements text[], name text, created_by text, idempotency_key text, rollback text[] — NO timestamp; dates only from the version string. 128 rows, min '083', max 20260722010000.
- Only 20 of 128 CLI rows have a file in supabase/migrations/ (name-join: 108 rows with no file — create_orchestrator_tables, arch_018_multi_agent_bus, bug024_phase1_agent_messages_provenance, cto_council*, all ihsanos 012_storefront…ledger_049_qurban, all ai-scholar al_bayan_*/mizan_*). The CLI history that built most of the substrate was deleted from the repo. HIGH for forensics (pre-962 era).
- 3 files with no table row: constraint5_retry_after, inbox_cadence_section_e, task032_category_parent_ref (likely applied under near-names). LOW.
- supabase db push after decision 962 (created_at 2026-05-25 20:22Z, RO on strategic_decisions id=962): every row with version > 20260525 was written by an explicit INSERT inside the migration file (docs/substrate/migration-conventions.md Convention 3): `grep -l schema_migrations supabase/migrations/20260527*.sql …202606*.sql` -> all 4 hit; migrations/012,014,030,031 likewise. 020-024 rows come from the feat/* branch files. statements for 024/030/031 are prose summaries, not SQL — hand INSERT, not CLI. NO evidence of a CLI push after 962. Pre-962 ihsanos rows (2026-04-06…04-20, created_by sheikh.musa@outlook.com) are monolith-era and explain the ihsanos objects in section 5.
- Row '083' operator_messages_defer: non-timestamp version, empty statements, no created_by; paired ledger row has no file (1b). MEDIUM.
- In migration_ledger but not CLI table: 001-019 and 031+ (by design). In CLI but not migration_ledger: 024_coordinator_panes (20260712220000), 030, all pre-July history. nervous_system/orch_self_audit.py:302 still reconciles by name against the CLI table, so the two ledgers disagree on "applied".
Remediation: make migration_ledger the single applied-ledger (orch_self_audit.py reads it); import the 128 CLI rows with note legacy-cli; drop Convention 3.

---

## 3. VIEW DRIFT (19 views)
Method: last CREATE [OR REPLACE] VIEW per view across migrations/*.sql (numbered order) -> supabase/migrations -> schema.sql; normalised line diff vs pg_get_viewdef (dump/views.tsv).

- boot_briefing — HIGH. Latest full-body file supabase/migrations/20260602_cc_session_costs_bigint_tokens.sql (19 arms). Live (RO `select source,count(*) from boot_briefing group by 1` + arm scan): 18 arms = repo_context, repo_snapshot, active_decision, open_qa_failure, latest_cc_session, latest_digest, last_cai_session, unverified_decisions, manual_override_bugs, inbox_sla_violation, paused_job_review_needed, paused_job_permanent_review, synthetic_filter x2, ralph_state, cc_session_costs, inbox_hygiene, data_provenance_flag. Live LACKS 3 arms the migration has (active_autonomous_loops, long_running_caller, watchdog_monitored_callers — removed by scripts/apply_boot_briefing_diet.py, "inline arm-surgery, no file", deleted in PR #88 a083c26) and HAS 1 arm no migration has (data_provenance_flag, added by scripts/extend_boot_briefing_arm.py --apply, gate #51717, by design per 089 lines 56-57). Columns unchanged: source text, key text, context json. No file in the repo reproduces live; schema.sql is stale too. Remediation: snapshot pg_get_viewdef into a numbered migration + refresh schema.sql; keep extend_boot_briefing_arm.py as the only mutation path.
- share_lane_status_v — MEDIUM. Live has extra columns state, context_pct, observed_at + LEFT JOIN pane_context; not in 045 nor any file (one-off applier deleted in PR #89, `git log --all -S context_pct`).
- agent_message_stale_claims, fleet_drain_freshness — MEDIUM. No CREATE VIEW anywhere; only `git log --all -S` -> c1aa7c4 (2026-08-17 cc-fleet-health). Hand-applied.
- decision_audit_state — HIGH (governance). Only in reports/backend-review/cai1293-APPLIED-20260823.sql + nazim branch 052.
- cto_council_open_sessions, stale_blockers — MEDIUM. No source local (CLI history deleted).
- finance_burn — OK (amount_usd_month -> amount_sgd_month is 019's RENAME propagating).
- lane_tasks_state, share_pool_status_v, fleet_proposal_metrics_v, invariant_registry_state, inbox_sla_violations, held_commitments_due, data_provenance_flags, operator_ask_turnaround_daily, agent_observed_activity, open_blocking_tasks, stale_agents — OK (pg_get_viewdef formatting only).

---

## 4. FUNCTION / TRIGGER / POLICY DRIFT
33 SECURITY DEFINER live. Body-normalised compare vs latest CREATE FUNCTION (fn_drift.txt).
MATCH: post_agent_message, mark_delivered, ack_message (117), agent_messages_cai_gate, strategic_decisions_cai_gate (064), classify_data_provenance (089), admin_mark_offline (038), project_governance_audit_trigger / _forbid_delete (063), get_decision / get_repo_context (schema.sql), populate_strategic_decisions_provenance (20260424).

HIGH — sweep_stale_agents(interval,text,text) EXISTS live although migration 040 (ledgered applied 2026-08-07 10:49 orch-console, CAI-766) is `DROP FUNCTION IF EXISTS public.sweep_stale_agents(interval, text, text)`.
Proof: RO `select p.oid::regprocedure, p.prosecdef, p.proacl from pg_proc p where proname='sweep_stale_agents'` -> `sweep_stale_agents(interval,text,text) | True | {=X/postgres,postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,service_role=X/postgres}`; body == 009. Bus #31074 (cc-fleet-health 2026-08-21 17:21Z): "Reaper RESTORED + verified (sweep_stale_agents re-applied, direct psycopg)" after orch-console #31068 "Reaper GONE". 040 applied, then reverted by hand with NO migration and NO ledger row; the re-create also undid 010's `REVOKE EXECUTE … FROM PUBLIC, anon, authenticated` (anon/authenticated now EXECUTE a SECDEF that offlines agents). Remediation: decide 009-vs-040 explicitly; if the reaper stays, ship a numbered migration re-creating it WITH the 010 REVOKE / GRANT-to-service_role, ledger it, amend 040's note.

MEDIUM — enforce_challenge_window_timeouts (SECDEF) live != supabase/migrations/20260424_batch1_structural_integrity.sql: live adds `if decision_audit_required(rec.decision_ref) then return query select rec.decision_ref,'skipped_audit_required'; continue; end if;`. Source = nazim branch / cai1293 report, not a tree migration.

Functions live with NO defining file in migrations/, supabase/migrations/, schema.sql (216 of 261 by regex; excluding extension-provided pgvector/http/dblink):
- decision-audit family (HIGH — gates strategic_decisions): audit_board_digest [SD], enforce_audit_tier_change_guard [SD], escalate_full_tier_without_auditor [SD], escalate_stale_decision_audits [SD], decision_audit_actor_norm, decision_audit_conflict, decision_audit_effective_builder, decision_audit_required, decision_audit_tier_candidate, decision_audit_unresolved, enforce_decision_audit_not_self, enforce_decision_audit_resolution_independence, enforce_challenge_window_requires_expiry.
- populate_agent_messages_provenance [SD] — CLI row bug024_phase1_agent_messages_provenance exists, file deleted. MEDIUM.
- trigger_cto_strategist [SD], handle_new_user [SD], sync_org_memberships_to_jwt [SD], auth_user_org_ids / _with_role / _with_roles [SD] + *_fast x3, auth_user_hr_employee_id [SD], calculate_platform_fee, next_receipt_number, next_pos_transaction_number, enforce_balanced_journal, update_updated_at, update_wingmen_features_updated_at, exec_work_items_bump_updated_at — ihsanos / cto-council / exec-broker, CLI history deleted or hand-applied. MEDIUM.
- ai-scholar (residency slice): get_root_* / get_surah_* / get_verb_form_stats / resolve_ayah_refs [SD], search_*, mizan_*, ruling_audit_log_fill_chain, match_juridical_embeddings.

Triggers with no migration (16 of 37): trg_audit_tier_change_guard, trg_challenge_window_requires_expiry (both on strategic_decisions — HIGH), trg_decision_audits_not_self, trg_decision_audits_resolution_independence, trg_cto_strategist_fire, trg_exec_work_items_updated_at, trg_wingmen_features_updated_at, ruling_audit_log_fill_chain_trg, mizan_auto_scores_flag_hallucination, mizan_judge_shadow_flag_hallucination_trg, trg_{donations,donation_categories,organizations,organizations_fiscal_config,persons,profiles}_updated_at.

Policies with no migration naming them: 94 of 289 (objs_vs_repo.txt). Orchestrator-relevant: decision_audits_{console_ro,service_only}, decision_tier_changes_{console_ro,service_only} (only in reports/cai1293-APPLIED), decision_tier_escalations_service, audit_board_digest_log_{console_ro,service}, exec_work_items_{runner_select,runner_update,service_all}, exec_delivery_ledger_{runner_ins,runner_sel,service_all} (role exec_runner), deny_all_schema_drift_findings, deny_all_shared_feed_dedup, cto_council* / wingmen_config / wingmen_features service_role_only_*. Rest = ihsanos + ai-scholar. HIGH for decision_* and exec_* (governance + broker with no reproducible source).

---

## 5. Tables
Live with NO CREATE TABLE in migrations/, supabase/migrations/, schema.sql — 50:
- governance, hand/branch-applied (HIGH): decision_audits, decision_tier_changes, decision_tier_escalations, audit_board_digest_log.
- exec broker (MEDIUM): exec_work_items, exec_delivery_ledger — NO ledger row (`select migration_name from migration_ledger where migration_name ilike '%exec_%'` -> only hc95_exec_broker_access_log); vault_access_log still live beside exec_broker_access_log after the #442 rename.
- feat-branch (LOW): schema_drift_findings, shared_feed_dedup.
- CLI-history-deleted (MEDIUM): cto_council, cto_council_sessions, wingmen_config, wingmen_features, change_requests, claim_tokens, onboarding_events, sites, site_links, user_unlocks, accounts, data_exports, audit_log, audit_key_registry, audit_portal_config, daily_attestations, ingestion_provenance.
- ihsanos monolith-era (residency slice): donations, donation_categories, receipts, person_roles, person_relationships, org_role_permissions, organizations_fiscal_config, profiles.
- ai-scholar (residency slice): ayat, ayat_topics, ayah_themes, topics, tafsir_entries, tafsir_embeddings, asbab_nuzul, asbab_embeddings, hadiths, hadith_collections, hadith_embeddings, juridical_texts/translations/embeddings, mutashabihat, mizan_auto_scores, mizan_judge_shadow, ruling_audit_log.

In migrations/ but NOT live — 3: quality_gate_runs (024, never applied); reel_inbox (001 ledgered applied 2026-07-12 cc-infra, `to_regclass('public.reel_inbox')` NULL, no DROP TABLE reel_inbox anywhere in repo -> dropped by hand, MEDIUM); coordinator_panes.changed_at (034, never applied). drafts/life_graph_p1.sql tables not live; drafts/nazim_console_channel.sql effect IS live (1 bot_channels row matching 'nazim') without a ledger row.

---

## Top findings by severity
1. HIGH — sweep_stale_agents SECDEF live with anon/authenticated EXECUTE, contradicting ledgered 040 (DROP) and 010 (REVOKE); restored by direct psycopg 2026-08-21 (bus #31074), no migration/ledger.
2. HIGH — decision-audit governance family (4 tables, 13 functions incl. 4 SECDEF, 4 triggers on strategic_decisions/decision_audits, 7 policies, view decision_audit_state, live-edited enforce_challenge_window_timeouts) has no migration on the mainline — only backfill ledger rows pointing at an unmerged branch and a reports/…APPLIED SQL.
3. HIGH — boot_briefing live body (18 arms) matches no file: 3 arms removed + 1 added by deleted/one-off scripts; schema.sql stale.
4. HIGH — 108 of 128 CLI schema_migrations rows have no file in the repo; ~100 substrate objects have no reproducible source.
5. MEDIUM — 14 migration files applied-unledgered, 2 never applied, 13 sha mismatches (11 header-comment edits in PR #92; 072/086 unreproducible), ledger row 083 with no file in any branch + off-convention '083' CLI row, reel_inbox ledgered-applied but gone.
6. OK — no supabase db push evidence after decision 962; all bus/cai-gate functions match their migrations.
