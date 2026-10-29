-- bariq_editor_rls_ceayj.sql
-- ledger: silo=ceayjeamtmcyzzvqflus
-- assert: no_table_privilege bariq_editor public.bariq_site_content INSERT
-- assert: no_table_privilege bariq_editor public.bariq_site_content DELETE
-- assert: no_table_privilege bariq_editor public.bariq_site_content TRUNCATE
-- assert: no_table_privilege bariq_editor public.bariq_site_content_history INSERT
-- assert: no_table_privilege bariq_editor public.bariq_site_content_history UPDATE
-- assert: no_table_privilege bariq_editor public.bariq_site_content_history DELETE
-- assert: no_table_privilege bariq_editor public.bariq_site_content_history TRUNCATE
--
-- RLS policies for bariq_editor on bariq_site_content(_history) — the editor
-- deny-all fix (orch-console #77536, diagnosed from the live DB with the
-- auditor role). The provisioning migration (bariq_site_content_ceayj.sql)
-- created the tables, minted the bariq_editor grants, and added the anon
-- SELECT policy — but NO policy FOR bariq_editor: with RLS enabled and
-- bypassrls=f, table grants alone admit nothing; every SELECT/UPDATE as the
-- editor filters to 0 rows (GRANT-without-policy = deny-all).
--
-- Policies below mirror the exact grants provision_bariq.py mints (and
-- verifies): SELECT + UPDATE on content, SELECT on history — all pinned to
-- the Bariq org row. INSERT/DELETE remain impossible everywhere (no table
-- grants; asserted above). Split per-command policies instead of FOR ALL so
-- the policy surface itself can never exceed the grant surface.
--
-- Additive and idempotent (DROP POLICY IF EXISTS + CREATE POLICY). No table,
-- grant, or role changes. Applied by orch-console behind the gate (credential
-- slice); cc-bariq is residency-barred from the ceayj write DSN.
--
-- REVERT: DROP POLICY IF EXISTS bariq_site_content_editor_select ON public.bariq_site_content;
--         DROP POLICY IF EXISTS bariq_site_content_editor_update ON public.bariq_site_content;
--         DROP POLICY IF EXISTS bariq_site_content_history_editor_select ON public.bariq_site_content_history;

DROP POLICY IF EXISTS bariq_site_content_editor_select ON public.bariq_site_content;
CREATE POLICY bariq_site_content_editor_select ON public.bariq_site_content
  FOR SELECT TO bariq_editor
  USING (org_id = '62617269-7100-4000-8000-000000000001');

DROP POLICY IF EXISTS bariq_site_content_editor_update ON public.bariq_site_content;
CREATE POLICY bariq_site_content_editor_update ON public.bariq_site_content
  FOR UPDATE TO bariq_editor
  USING (org_id = '62617269-7100-4000-8000-000000000001')
  WITH CHECK (org_id = '62617269-7100-4000-8000-000000000001');

DROP POLICY IF EXISTS bariq_site_content_history_editor_select ON public.bariq_site_content_history;
CREATE POLICY bariq_site_content_history_editor_select ON public.bariq_site_content_history
  FOR SELECT TO bariq_editor
  USING (org_id = '62617269-7100-4000-8000-000000000001');
