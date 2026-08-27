BEGIN;
SET LOCAL statement_timeout='90s';
-- 206_cai1199_cross_source_donor_leaderboard.sql
-- [CAI-1199] Cross-source donor leaderboard (donations + umum person-tins) +
-- durable sch_students minors-exclusion on donations_top_donors (Track-2).
--
-- SOURCES (coord #29499 ruling): donations(person_id) + tabung_umum_tins person-
-- collector tins. The keluarga (kk) source is EXCLUDED — tabung_kk_tins has NO
-- person_id (CAI-1202 person_id-only => empty) and its money already flows into
-- donations via donation_id (CAI-643 double-count). Verified at source (goumlyne
-- 2026-08-20): umum person tins never populate donation_id (0/390), and none of
-- their donation_ids appear in donations => donations + umum are DISJOINT money,
-- safe to sum.
--
-- MINORS-EXCLUSION: both arms carry the STRUCTURAL sch_students NOT-EXISTS
-- (== the persons-RLS carve-out, mig192) so a student-linked donor is never
-- ranked. person_id IS NOT NULL (a named-donor leaderboard; the anonymous NULL
-- bucket is not a donor identity).
--
-- RLS-THROUGH (NOT service-role — the CAI-1192 root cause): SECURITY INVOKER +
-- STABLE, run under the CALLER's RLS. donor_leaderboard_all is called from an
-- org_admin-ONLY action via the caller (session) client. org_admin has RLS SELECT
-- on donations, tabung_umum_tins AND sch_students (verified), so the aggregation
-- is complete AND the exclusion subquery sees the students it must exclude.
--
-- Mirrors umum_top_donors (mig082/204): person-collector tins, status
-- ('banked','closed'), SUM(amount_total).

CREATE OR REPLACE FUNCTION public.donor_leaderboard_all(
  p_org_id uuid,
  p_limit  integer DEFAULT 10
)
RETURNS TABLE(person_id uuid, total numeric)
LANGUAGE sql
STABLE
SET search_path TO 'public', 'pg_temp'
AS $function$
  SELECT u.person_id, SUM(u.total)::NUMERIC AS total
  FROM (
    -- ── donations arm ──────────────────────────────────────────────────────
    SELECT d.person_id, COALESCE(SUM(d.amount), 0)::NUMERIC AS total
    FROM donations d
    WHERE d.deleted_at IS NULL
      AND d.org_id = p_org_id
      AND d.person_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM sch_students ss
        WHERE ss.person_id = d.person_id AND ss.org_id = p_org_id
      )
    GROUP BY d.person_id

    UNION ALL

    -- ── umum person-tin arm (banked/closed only; mirrors umum_top_donors) ───
    SELECT t.person_id, COALESCE(SUM(t.amount_total), 0)::NUMERIC AS total
    FROM tabung_umum_tins t
    WHERE t.deleted_at IS NULL
      AND t.org_id = p_org_id
      AND t.collector_type = 'person'
      AND t.status IN ('banked', 'closed')
      AND t.person_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM sch_students ss
        WHERE ss.person_id = t.person_id AND ss.org_id = p_org_id
      )
    GROUP BY t.person_id
  ) u
  GROUP BY u.person_id
  ORDER BY total DESC
  LIMIT GREATEST(p_limit, 0);
$function$;

-- [CAI-1199 Track-2] Durable sch_students exclusion on donations_top_donors.
-- The app-layer org_admin-gate shipped in #389 is complemented here so the RPC
-- itself never ranks a student, matching umum_top_donors / jumaat_top_holders.
-- Signature + filters are otherwise UNCHANGED. Anonymous (person_id NULL) rows are
-- preserved (NOT EXISTS is TRUE for a NULL person_id).
CREATE OR REPLACE FUNCTION public.donations_top_donors(
  p_org_id uuid,
  p_from   timestamptz DEFAULT NULL,
  p_to     timestamptz DEFAULT NULL,
  p_limit  integer DEFAULT 5
)
RETURNS TABLE(person_id uuid, total numeric)
LANGUAGE sql
STABLE
SET search_path TO 'public', 'pg_temp'
AS $function$
  SELECT d.person_id, COALESCE(SUM(d.amount), 0)::NUMERIC AS total
  FROM donations d
  WHERE d.deleted_at IS NULL
    AND d.org_id = p_org_id
    AND (p_from IS NULL OR d.donated_at >= p_from)
    AND (p_to   IS NULL OR d.donated_at <= p_to)
    AND (
      d.person_id IS NULL
      OR NOT EXISTS (
        SELECT 1 FROM sch_students ss
        WHERE ss.person_id = d.person_id AND ss.org_id = p_org_id
      )
    )
  GROUP BY d.person_id
  ORDER BY total DESC
  LIMIT GREATEST(p_limit, 0);
$function$;

\echo '=== BASELINE: donor_leaderboard_all top 5 ==='
SELECT person_id, total FROM donor_leaderboard_all('73339164-7c1f-40ba-a093-33f1f292dd4c', 5);

CREATE TEMP TABLE _top1 AS SELECT person_id, total FROM donor_leaderboard_all('73339164-7c1f-40ba-a093-33f1f292dd4c', 1);
\echo '=== TOP-1 donor (will be turned into a student) ==='
SELECT * FROM _top1;

-- negative control: make the #1 donor a student
INSERT INTO sch_students (org_id, person_id, student_number)
SELECT '73339164-7c1f-40ba-a093-33f1f292dd4c', person_id, 'WETPROVE-206' FROM _top1;

\echo '=== AFTER: is the top-1 person still in the leaderboard? (expect 0 rows) ==='
SELECT l.person_id, l.total FROM donor_leaderboard_all('73339164-7c1f-40ba-a093-33f1f292dd4c', 200) l JOIN _top1 t ON t.person_id=l.person_id;

DO $$
DECLARE cnt int; base numeric; after_top numeric;
BEGIN
  SELECT count(*) INTO cnt FROM donor_leaderboard_all('73339164-7c1f-40ba-a093-33f1f292dd4c',200) l JOIN _top1 t ON t.person_id=l.person_id;
  IF cnt=0 THEN RAISE NOTICE 'PASS: top-1 donor EXCLUDED from donor_leaderboard_all after becoming a student';
  ELSE RAISE EXCEPTION 'FAIL: student still ranked in donor_leaderboard_all (cnt=%)', cnt; END IF;

  SELECT count(*) INTO cnt FROM donations_top_donors('73339164-7c1f-40ba-a093-33f1f292dd4c',NULL,NULL,200) l JOIN _top1 t ON t.person_id=l.person_id;
  RAISE NOTICE 'donations_top_donors rows for the now-student person: % (0 = durably excluded)', cnt;
END $$;

\echo '=== sanity: leaderboard still returns other donors (non-empty) ==='
SELECT count(*) AS remaining_donors FROM donor_leaderboard_all('73339164-7c1f-40ba-a093-33f1f292dd4c', 50);

ROLLBACK;
\echo '=== ROLLED BACK — no writes persisted ==='
