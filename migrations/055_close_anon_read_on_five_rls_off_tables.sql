-- 055 — close anon/authenticated reach on the five RLS-OFF fleet tables.
--
-- NUMBERING NOTE: applied to the substrate while numbered 054, then renumbered to 055.
-- cc-fleet-health committed its own 054 (b8e19dd, sla observed-recipient-activity) 44s
-- before this one (12411c5) — two bodies picked the next free number in the same minute.
-- The DB state is unaffected by the rename; the earlier commit keeps the number.
--
-- WHAT WAS WRONG (found 2026-08-17 ~06:45Z, orch-console; PROVEN over the wire by
-- cc-quality with the anon key, limit=0/Range 0-0, zero rows pulled):
--   held_commitments        206, content-range */12   <- migration 051, shipped 2026-08-16
--   fleet_proposals         206, content-range */45
--   chat_members            206, content-range */3    <- PERSONAL DATA (usernames, display names)
--   audit_chain_boundaries  206, content-range */1    <- client project_refs + org_ids
--   revenue_ledger          200, content-range */0    <- empty today; latent, not safe
-- All five had RLS OFF with ZERO policies, `anon` holding SELECT and `authenticated`
-- holding INSERT/UPDATE/DELETE. RLS off means there is no policy — the grant was the
-- only layer, and it was open. Anonymous internet callers could read fleet governance
-- rows, and any `authenticated` JWT on this project could DELETE held_commitments,
-- which carries commitment #12, the PII delete-backstop and its quarantine path.
--
-- THE CLASS (CAI-1041, restated): an object inherits a permissive platform default and
-- nothing asserts otherwise at ship time. Supabase seeds default privileges that grant
-- anon/authenticated on new objects in `public`; the same class produced two
-- default-PUBLIC-EXECUTE function holes the same night. See
-- reports/backend-review/cai1041-permissive-default-inheritance-measured.md.
--
-- WHY NOT A BLANKET REVOKE ACROSS EVERY RLS-OFF TABLE: migration 031 records that
-- ruling_audit_log, audit_key_registry, daily_attestations and ingestion_provenance are
-- cryptographic-transparency surfaces where anon READ IS DELIBERATE. `audit_chain_boundaries`
-- merely shares the `audit_` prefix — it is forensic internal state naming client project
-- refs, and it is NOT in that set. Each of the five below was checked individually against
-- that list and against its code consumers before being included here.
--
-- CONSUMERS CHECKED FIRST (the near-outage discipline: before revoking, read who depends
-- on the grant — a policy-referenced or code-referenced grant is load-bearing):
--   held_commitments  -> nervous_system/commitment_sweeper.py (direct psycopg as postgres)
--   fleet_proposals   -> scripts/propose.sh (direct psycopg as postgres)
--   chat_members      -> no code consumer, BUT `console_readonly` holds an explicit SELECT
--                        grant, so a policy below preserves exactly that access and no more.
--   audit_chain_boundaries, revenue_ledger -> no code consumer.
-- `postgres` and `service_role` are BYPASSRLS, so every orchestrator process is unaffected.
-- `console_readonly` is NOT bypassrls, hence the one explicit policy.
--
-- REVERSIBLE: to undo, DISABLE ROW LEVEL SECURITY and re-GRANT. Nothing is dropped.

BEGIN;

-- ── 1. RLS on (defence layer that was entirely absent) ──────────────────────────
ALTER TABLE public.held_commitments       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fleet_proposals        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_members           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_chain_boundaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.revenue_ledger         ENABLE ROW LEVEL SECURITY;

-- ── 2. preserve console_readonly's existing, deliberate SELECT on chat_members ──
-- Exactly what it holds today, no wider. Without this, enabling RLS would silently
-- narrow an access someone granted on purpose — changing something we did not intend
-- to change is its own defect, not a bonus.
DROP POLICY IF EXISTS chat_members_console_readonly_select ON public.chat_members;
CREATE POLICY chat_members_console_readonly_select
    ON public.chat_members FOR SELECT TO console_readonly USING (true);

-- ── 3. remove the grants that were the only (open) layer ────────────────────────
REVOKE ALL ON public.held_commitments       FROM anon, authenticated;
REVOKE ALL ON public.fleet_proposals        FROM anon, authenticated;
REVOKE ALL ON public.chat_members           FROM anon, authenticated;
REVOKE ALL ON public.audit_chain_boundaries FROM anon, authenticated;
REVOKE ALL ON public.revenue_ledger         FROM anon, authenticated;

COMMIT;
