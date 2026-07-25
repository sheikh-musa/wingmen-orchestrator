# Xendit demo — TEARDOWN MANIFEST (hub backstop) — restore wingmen-personal after Mon 07-20 call

**Project:** wingmen-personal — Supabase ref `brrgastulcffamlbggyu` (operator's personal project, SG).
**What happened:** on 2026-07-17 ~18:12–18:14 the Xendit demo lane bootstrapped the ihsanos schema (97 migrations, 5→89 public tables + seed rows) into this project. Operator chose **Option A** (op#5180 / Nazim #9670): keep the demo here through Monday, then DROP all demo objects — restoring the project to its original 5 tables. This manifest is the hub-owned backstop (the lane keeps its own in the worktree, which gets torn down — this copy survives).

**Commitment to operator (Nazim #9670 pt4):** after the Monday call, drop every demo object, leaving zero residue. His personal data must be byte-identical afterward.

## ✅ TESTED RUNNABLE SCRIPT
`reports/xendit-demo-teardown.py` — implements this manifest. DRY-RUN by default; real run needs
`--execute --i-understand-this-drops-the-demo`. Tested 2026-07-18 (`--self-test`): drop-set computed
from live metadata = **84** (89 public − 5 personal), preserve = the 5 personal tables, mamadah=33,
operator tracker rows=2, 95 bootstrap rows to delete; the CASCADE-drop mechanism was proven on a
throwaway schema (public untouched); predicted post-state = exactly the 5 personal tables + mamadah 33.
Run: `SUPABASE_ACCESS_TOKEN=… python3 reports/xendit-demo-teardown.py --self-test` (dry) then
`… --execute --i-understand-this-drops-the-demo` AFTER the Monday call.

## PRESERVE — NEVER DROP (operator's real data)
- `life_commitments`, `life_entities`, `life_relationships`, `mamadah_sources` (his life-graph app; 0 rows each as of bootstrap)
- `mamadah_notes` — **33 rows, created 2026-06-30** (his real notes). Verify 33 rows survive teardown.
- Any non-`public` schema he owns (none identified beyond Supabase-managed auth/storage/realtime/vault).

### LANE ADDENDUM 2026-07-18 (cc-ihsanos) — precise inventory + CORRECTIONS
**More PRESERVE items the hub draft missed (verified via mgmt query endpoint):**
- **Personal functions (6):** `life_intimate_guard`, `life_isolation_audit`, `life_neighbours`, `life_purge_entity`, `life_recall`, `life_slice_can_see` — his life-graph logic. NEVER drop.
- **`vector` (pgvector) extension** + ALL its functions (`vector_*`, `halfvec_*`, `sparsevec_*`, `l2_distance`, `cosine_distance`, `inner_product`, `l2_norm/normalize`, `binary_quantize`, `subvector`, `hamming/jaccard_distance`, vector `avg`/`sum` aggregates, `*handler`, `*_support`). Powers his semantic search. Also keep `supabase_vault`, `pgcrypto`, `uuid-ossp`, `plpgsql`, `pg_stat_statements`. **Drop NO extensions.**
- **SHARED-NAME functions** `handle_new_user()` + `update_updated_at()`: the bootstrap ran `CREATE OR REPLACE` on these (generic names). If he had his own, the BODIES are now ihsanos'. **Do NOT drop** (could break a personal trigger); he restores his originals if wanted. No row data was changed.

**CORRECTION to §3 (tracker was NOT empty before):** `supabase_migrations.schema_migrations` PRE-EXISTED with **2 operator rows** — `created_by='sheikh.musa@outlook.com'`, versions `20260702055007` (015_life_graph_p1) + `20260702055211` (016_mamadah_second_brain). Teardown MUST delete ONLY my rows:
`DELETE FROM supabase_migrations.schema_migrations WHERE created_by='bootstrap-mgmt-api';` (95 rows). PRESERVE the 2 operator rows; do NOT drop the schema/table.

**Storage buckets added (4) — drop after verifying empty/ihsanos-only:** `payment-proofs`, `qurban-milestones`, `receipts`, `tabung-slips` (the personal project had none).

## DROP after Monday — the 84 ihsanos tables bootstrapped today
```
admin_actions, audit_log, bank_keyword_mappings, donation_categories, donations, fee_schedule, fee_schedule_audit, gl_accounts, gl_entries, gl_periods, gl_transactions, hr_attendance, hr_claims, hr_departments, hr_employees, hr_leave_allocations, hr_leave_types, hr_leaves, hr_payroll_runs, hr_payslips, inv_customers, inv_invoices, inv_line_items, inv_payments, inv_quotations, inv_recurring_templates, inv_time_entries, org_members, org_role_permissions, organizations, organizations_admin_settings, organizations_fiscal_config, outlet_sessions, outlets, person_roles, persons, platform_admins, pos_coupons, pos_inventory, pos_order_counters, pos_order_items, pos_orders, pos_product_modifiers, pos_product_variants, pos_products, pos_sessions, pos_supplier_payments, pos_suppliers, pos_time_slots, pos_transaction_items, pos_transactions, profiles, qa_findings, qbn_animals, qbn_bookings, qbn_milestones, qbn_niyyah, qbn_seasons, qbn_suppliers, qbn_tawkeel_acknowledgments, receipts, sch_attendance, sch_class_subjects, sch_classes, sch_fees, sch_grades, sch_student_parents, sch_students, sch_subjects, sch_timetable, storefront_customers, storefront_delivery_zones, storefront_funnel_events, storefront_notifications, tabung_kk_batches, tabung_kk_tins, tabung_umum_tins, tabung_weekly_reports, telegram_users, tg_auth_replay, tg_identity_audit, tg_provision_attempts, ui_events, wingmen_features
```
(84 tables. All held 0 real rows except bootstrap seed: `organizations`=2, `audit_log`=17, `fee_schedule`=4, `organizations_*config`=3 + whatever synthetic seed the demo adds. Snapshot captured pre-demo-seed; RE-CAPTURE the live public-table list at teardown time and DROP `public` minus the PRESERVE set, so any tables the synthetic seed adds are also caught.)

## Teardown procedure (execute AFTER the Monday call, via mgmt query endpoint on `brrgastulcffamlbggyu`)
1. Re-capture: `select tablename from pg_tables where schemaname='public'` — diff vs PRESERVE set = authoritative drop set (catches anything added since).
2. `DROP TABLE public.<each> CASCADE;` for every table NOT in PRESERVE. (CASCADE clears the ihsanos FKs among them.)
3. Clean the tracker: `DELETE FROM supabase_migrations.schema_migrations WHERE created_by='bootstrap-mgmt-api';` (95 ihsanos rows). **PRESERVE the 2 operator rows** (`created_by='sheikh.musa@outlook.com'`) — see LANE ADDENDUM correction. Do NOT drop the schema/table. Also drop the 4 ihsanos storage buckets (verify empty first) and, optionally, the 33 ihsanos-only functions (never `life_*`, `handle_new_user`, `update_updated_at`, or pgvector fns).
4. Remove the Vercel preview project + its env (lane to record the exact project id/URL here when deployed).
5. VERIFY: `public` has exactly the 5 PRESERVE tables; `mamadah_notes` = 33 rows; no ihsanos tables remain. Report done to Nazim.

## Vercel / env artifacts (DEPLOYED 2026-07-18)
- **Vercel project:** `ihsanos-xendit-demo` — id `prj_5rTULjImls9Sn4s8rr42OnRbJZu0`, team `wingmen` (`team_mYxOkemmlg8a3HnKFAE9di7N`). **Delete after Monday** (`vercel projects rm` or dashboard) — removes the deployment + all its env vars.
- **Demo URL (stable):** https://ihsanos-xendit-demo.vercel.app  (per-deploy: …-9yyv6et4k… preview + …-minb9v2fc… prod)
- **Env vars set on the project (prod+preview):** NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, NRIC_ENCRYPTION_KEY, XENDIT_SECRET_KEY, XENDIT_WEBHOOK_TOKEN, XENDIT_MODE, NEXT_PUBLIC_XENDIT_QRIS_ENABLED (=true, QRIS greenlit), XENDIT_FX_SGD_IDR_RATE (=11680, pinned). Gone when the project is deleted.
- **SSO deployment protection was DISABLED** on this project to make the preview public (operator + Xendit webhook access). Deleting the project removes it; no other project affected.
- Local: `~/wingmen/ihsanos-wt/xendit-pos/.env.demo.local` + `.env.local` (chmod600, gitignored) — delete post-call.

## Synthetic auth.users added (remove at teardown — auth.users is Supabase-managed, NOT dropped by §1)
Created via seed / admin API / onboarding for the demo + product review. Delete these auth users
(and their cascade) at teardown; the operator's own auth users are untouched:
- `admin@bapa.test`, `cashier@bapa.test`, `nz-halal@bapa.test`, `sg-abattoir@bapa.test`, `teacher@bapa.test`, `parent@bapa.test` (seed-bapa-test-roles)
- `musa@gazzabyte.com`, `volunteer@bapa.org.sg` (seed.ts)
- `demo.merchant.5187@gmail.com` (onboarding capture) + any `demo-merchant-p5187-*@synthwingmen.test` (failed `.test` signup attempts)
Also removes their `profiles` rows (public.profiles is in §1). Org "Demo Merchant (Synthetic)" is an
`organizations` row (dropped in §1).

## Demo-specific DB tweaks (all inside ihsanos tables → removed by §1 table drops)
- `organizations.slug='bapa'` set on the BAPA demo org; `outlets` row `YSH294` (lite) + one `outlet_sessions` row; re-added `persons.email`/`phone` columns (reversing 064 so the role seed completes). All live in the ihsanos tables dropped in §1 — no separate teardown.
