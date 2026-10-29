# irsyad_monitor_ro: runbook

Console #81213 item 4, rulings #82720. Narrowed read-only role for the irsyad monitors on goumlyne.
`auditor_ro` stays broad, but only for the backup and the agent-exec reconcile.

Files:
- `irsyad_monitor_ro_goumlyne.sql`: the migration (goumlyne only, never ceayj).
- `irsyad_monitor_ro_qa_proof.sql`: the QA proof (irsyad-qa only). It ends in `RAISE EXCEPTION 'QA_PROOF_RESULT ...'`, which rolls everything back. Run it first and read the result lines: every line must be PASS (or INFO).
- `tests/test_irsyad_monitor_ro_sql.py`: keeps the proof's included copy byte-identical to the migration.

## 0. QA proof (before anything on goumlyne)
Run `irsyad_monitor_ro_qa_proof.sql` on irsyad-qa (ref `oovskwogvefnznfowjky`) through the exec path. The expected outcome is an error whose message starts `QA_PROOF_RESULT overall=PASS fails=0`. The file refuses to seed if org `73339164` exists, so it aborts on goumlyne.

## 1. Apply on goumlyne
Apply `irsyad_monitor_ro_goumlyne.sql` on goumlyne through the gated exec path (`--gate` naming `goumlyne`, this PR, and console #81213/#82720). The file has no BEGIN/COMMIT of its own. Its final DO block asserts:
- the role's attributes;
- the role is a member of no other role;
- `default_transaction_read_only=on`;
- RLS is on for all 7 tables;
- SELECT on exactly 7 public tables, and no write privilege anywhere in public;
- EXECUTE on the 3 helpers. There is no USAGE on `auth`: postgres holds no grant option there, so `auth.uid()` is not nameable by this role (42501), which the QA proof asserts;
- the 14 policies.

If any of these fails, the transaction rolls back. The role is created **NOLOGIN**.

**Expected after apply (console #83206):** `postgres` is a member OF `irsyad_monitor_ro`, with grantor `supabase_admin`, admin_option=true, inherit_option=false and set_option=false. This is PG16's automatic grant to a non-superuser role's creator. It lets postgres ALTER and DROP the role, which section 2 (LOGIN PASSWORD) and section 7 (DROP ROLE) need. It cannot use or SET ROLE to the role's privileges. Leave it; do not revoke it.

Applied on goumlyne 2026-10-29 under gate #83193. The owner-side read-back was accepted in #83206.

## 2. Secrets holder: enable login and build the DSN
Run once, out-of-band, never in a file or the transcript:
```
ALTER ROLE irsyad_monitor_ro LOGIN PASSWORD '<generated>';
```
Then build the pooler DSN (same host and port as the existing goumlyne pooler DSN), with user `irsyad_monitor_ro.goumlynecruxrlmzlntp` and `sslmode=require`.

## 3. Point the monitors at the new DSN
In the secrets bundle that the gzbai monitors source (`/dev/shm/wingmen-secrets/.env`), set `GOUMLYNE_RO_DATABASE_URL` to the new DSN. Consumers that stay on this variable, all compatible with the 7-table, irsyad-only scope:

| Consumer | Where | What it reads |
|---|---|---|
| PII monitor (cron `:23`) | `scripts/irsyad_pii_containment_monitor.py:37`, tick `/home/gazzai/irsyad_pii_containment_monitor_tick.sh:7` | persons, sch_students, sch_student_parents, information_schema.columns |
| Cisco watch (cron `:37`) | `scripts/cisco_reconcile_watch.py:34`, tick `/home/gazzai/cisco_reconcile_watch_tick.sh:6` | tabung_coin_deposits |
| Weekly audit-chain check (cron Mon 09:00, gzbai) | `ops/audit-chain-weekly/weekly_audit_chain_check.py:65`, reading `orchestrator/.env` per `:52` | audit_log WHERE org_id = irsyad |
| Rederive dump (manual) | `ops/audit-chain-amnesty/rederive_dump.py:18` | audit_log WHERE org_id = irsyad |
| Issue digest | `scripts/irsyad_daily_issue_digest.py:27` | ui_events + org_members. Dormant: last sent 30 Jul, not scheduled anywhere |

Notes on these consumers:
- **Cisco watch:** `cisco_reconcile_watch.py:181`, an unscoped `SELECT count(*)` on tabung_coin_deposits, now counts irsyad rows only. Before, it counted the whole silo, including the second (QA) org. This is intended, but the logged total may drop.
- **Weekly audit-chain check:** it reads `GOUMLYNE_RO_DATABASE_URL` from `orchestrator/.env`, so set the new DSN there too. See step 4 for why the auditor DSN must leave that file.

## 4. Move the auditor_ro DSN to the backup variable, and rotate it
- Rotate `auditor_ro`'s password at the same time: `ALTER ROLE auditor_ro PASSWORD '<new>'`, run by the secrets holder. This makes the old DSN, which has been in lane-sourced bundles, dead everywhere.
- Hold the new auditor_ro DSN as **`GOUMLYNE_BACKUP_RO_DATABASE_URL`** in only two places:
  - **Mac Mini**: the env used by `daily_backup.sh` (launchd `dev.wingmen.daily-backup`).
  - **gzbai**: a dedicated mode-0600 file, for example `/home/gazzai/.config/irsyad-backup-ro.env`. Only `/home/gazzai/irsyad_exec_reconcile_tick.sh` sources it, through an added `set -a; . <file>; set +a` line after its current `:4`, which sources `orchestrator/.env`.
- Never put it in `/dev/shm/wingmen-secrets/.env` or `orchestrator/.env`, because lanes source those. Remove any auditor_ro DSN from both files. After step 3, `GOUMLYNE_RO_DATABASE_URL` in both files is the monitor DSN.

## 5. Code: consumers that must switch to `GOUMLYNE_BACKUP_RO_DATABASE_URL`
These need the whole silo, or schemas or tables outside the 7, so they break on the monitor role. Land these code changes before or together with step 3.

| Repo | File:line | Why |
|---|---|---|
| orchestrator | `scripts/daily_backup.sh:346` (`backup_client_silo "irsyad-goumlyne" "${GOUMLYNE_RO_DATABASE_URL:-}"`); doc lines `:3-4` and `:328` | `pg_dump` of the whole public schema |
| ihsanos | `scripts/monitor/agent_exec_reconcile.py:52` (`"goumlyne": ("GOUMLYNE_RO_DATABASE_URL", "GOUMLYNE_PROJECT_REF")`); runs from `/home/gazzai/wingmen/reconcile-wt` | audit_log across all orgs, by actor |
| ihsanos | `scripts/monitor/irsyad_person_actor_audit_monitor.py:69` (`"goumlyne": "GOUMLYNE_RO_DATABASE_URL"`) | reads `auth.users` and `auth.sessions` (`:162`, `:183`, `:191`). Not in the gzbai crontab today, but must switch before any run |
| ihsanos | `scripts/import/dms-shop-donation-import.ts:23` | doc comment only ("reconcile uses GOUMLYNE_RO_DATABASE_URL"). The reconcile it describes reads donations, so update the comment to the BACKUP variable |

## 6. Verify
1. **Catalog, read-only via exec_prod** (plain SELECTs):
   - `SELECT rolcanlogin, rolsuper, rolbypassrls, rolinherit FROM pg_roles WHERE rolname='irsyad_monitor_ro'` should return `t,f,f,f` after step 2.
   - `SELECT count(*) FROM pg_policies WHERE policyname LIKE 'irsyad_monitor_ro_org_%'` should return 14.
2. **The monitors:** do NOT run them manually, because they page (lesson #81332). Watch the next scheduled runs instead: `/home/gazzai/irsyad_pii_containment_monitor.log` after `:23` and `/home/gazzai/cisco_reconcile_watch.log` after `:37`. Each needs a clean run with no connect or permission error, and counts unchanged from the previous auditor_ro run. An unexpected new P0 or P1 after the switch is a scope regression: roll back step 3.
3. **The backup and the reconcile:**
   - The next `daily_backup.sh` run has `irsyad-goumlyne` complete.
   - The next `irsyad_exec_reconcile.log` tick is clean.
4. **Old DSN is dead:** after the step-4 rotation, connecting with the old auditor_ro DSN fails with an auth error.

## 7. Rollback
- **Env:**
  - Put the auditor_ro DSN back as `GOUMLYNE_RO_DATABASE_URL` in the monitor bundle. Use the rotated one; the old one is dead.
  - Revert the step-5 code changes, or keep them with `GOUMLYNE_BACKUP_RO_DATABASE_URL` set.
- **DB:** on goumlyne, via the gated exec path, run in one transaction:
```
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.audit_log;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.audit_log;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.persons;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.persons;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.sch_students;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.sch_students;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.sch_student_parents;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.sch_student_parents;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.tabung_coin_deposits;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.tabung_coin_deposits;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.ui_events;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.ui_events;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.org_members;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.org_members;
REVOKE ALL ON public.audit_log, public.persons, public.sch_students, public.sch_student_parents,
              public.tabung_coin_deposits, public.ui_events, public.org_members FROM irsyad_monitor_ro;
REVOKE ALL ON FUNCTION public.auth_nonadmin_staff_student_person_ids(),
                       public.auth_subadmin_student_person_ids(),
                       public.auth_user_subadmin_org_ids() FROM irsyad_monitor_ro;
REVOKE USAGE ON SCHEMA public, auth FROM irsyad_monitor_ro;
ALTER ROLE irsyad_monitor_ro RESET ALL;
DROP ROLE irsyad_monitor_ro;
```
`DROP ROLE` fails if any privilege was missed. That failure is the check: find the privilege, revoke it, then drop the role.
