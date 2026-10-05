-- bedrock_substrate_deferred.sql — NOT a ledger-tracked migration (migrations/infra/ is
-- excluded from the normal `migrations/*.sql` walk and from `apply_migration.py`'s CLI).
--
-- Companion to bedrock_substrate_core.sql (see that file's header for full provenance).
-- These statements are split out because each forward-references an object this repo's
-- OWN migration chain creates LATER in the walk, not something bedrock_substrate_core.sql
-- (applied before schema.sql) can see yet:
--   - the `mamadah_sources` table, created by migrations/012 (1 FK)
--   - the `clients` table, created by schema.sql (5 client_id FKs)
--   - the `console_readonly` role + most of the tables it reads, both created by migrations
--     004/006/013/055 — ONLY `coordinator_panes` and `invariant_registry` genuinely have no
--     real-migration-authored console_readonly SELECT policy (cp#83, 2026-10-05): a prior
--     version of this file carried ~25 more console_readonly policy lines, written when the
--     bootstrap walk died long before migration 013 and never got far enough to discover that
--     migrations 004/006/013/055 already create those exact policies themselves (each with its
--     own `DROP POLICY IF EXISTS` guard). Once the walk reached migration 013 for real
--     (058→090 fixes landing this same day), every one of those duplicates surfaced as
--     `DuplicateObject`. Removed rather than no-op'd, since the real migrations are the
--     authoritative source. Methodology note: re-audit deferred/bedrock files for staleness
--     any time the walk reaches further than it did when the file was last authored.
-- scripts/ci_bootstrap_schema.py applies this file last, after the full migrations/*.sql walk.

ALTER TABLE ONLY public.mamadah_notes
    ADD CONSTRAINT mamadah_notes_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.mamadah_sources(id);

-- client_repos/provisions/usage_log client_id FKs deferred: public.clients is created
-- by schema.sql, which bedrock_substrate_core.sql runs before -- same forward-reference
-- class as mamadah_sources above.
ALTER TABLE ONLY public.client_repos
    ADD CONSTRAINT client_repos_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE ONLY public.usage_log
    ADD CONSTRAINT usage_log_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE ONLY public.revenue_ledger
    ADD CONSTRAINT revenue_ledger_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);

-- trg_agents_single_owner_repo_scope removed (cp#83, walk now reaches migration 043):
-- migrations/043_agents_single_owner_repo_scope_guard.sql creates BOTH
-- agents_single_owner_repo_scope() and this trigger itself (with its own
-- `DROP TRIGGER IF EXISTS` guard) -- this was a genuine duplicate, not a forward-
-- reference gap, caught by DuplicateObject once the walk actually reached 090+.

-- Only these two console_readonly SELECT policies have no real-migration-authored
-- equivalent (verified cp#83, 2026-10-05, via grep of every other CREATE POLICY name
-- that used to live in this file against migrations/*.sql schema.sql supabase/migrations/*.sql
-- — all others came back as duplicates of migrations 004/006/013/055, see header).
CREATE POLICY coordinator_panes_console_ro ON public.coordinator_panes FOR SELECT TO console_readonly USING (true);
CREATE POLICY invariant_registry_console_ro ON public.invariant_registry FOR SELECT TO console_readonly USING (true);
