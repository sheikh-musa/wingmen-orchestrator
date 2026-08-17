-- DRAFT (pending cai review + §6.6 grant) — promote to migrations/049_invariant_assertion_runs.sql on grant.
-- invariant_assertion_runs — the substrate-readable, JOINABLE run log for automated invariant measurers.
--
-- WHO/WHY: drafted by cc-fleet-health for the CAI-985 A3 automation; cai stewards invariant_registry
--   and REVIEWS + GRANTS this (CAI-RESP-1014 Q3 — steward gates the change, builder knows the sink's needs).
--
-- THE PROBLEM IT SOLVES (Nazim #24061 condition + cai Doctrine-1 / CAI-1000 D7): a reader of
--   invariant_registry.RESIDENCY-1 must be ONE JOIN from the actual PASS/FAIL verdict + evidence.
--   An alert is not enough — alerts scroll, get marked read, and die with the body that saw them.
--   gate_status carries the not-green (CAI-RESP-1014: COVERED on PASS, 'pending' on FAIL — so a live
--   leak can never read EXERCISED in the existing 047 view, by construction); THIS table carries the
--   verdict, the evidence, and run_at (proof the measurer RAN — so 'pending-failed' is distinguishable
--   from 'pending-never-ran').
--
-- ENCODING (CAI-RESP-1014):
--   PASS  -> invariant_registry gate_status='COVERED', last_asserted_at=now(); + a PASS row here (finding_count=0).
--   FAIL  -> invariant_registry gate_status='pending', last_asserted_at UNCHANGED (not proven true, so a
--            fail streak also goes stale); + a FAIL row here (finding_count>0, evidence=the offending grant rows).
--   ERROR -> (RULED IN by cai, CAI-RESP-1016 — 3-outcome PASS/FAIL/ERROR): the measurer RAN but
--            could not complete (e.g. ceayj unreachable, catalogue query failed). This is the D5
--            "a green that means I was not allowed to look" case and must NOT read as PASS nor
--            masquerade as a clean FAIL. gate_status='pending' (not green, D6 fail-closed), an
--            ERROR row here (finding_count=0, evidence=the error) — 'leak found' kept distinct from
--            'could not look' (wrong owner: an ERROR means fix the measurer, not residency).
--
-- POST-APPLY VERIFICATION IS REQUIRED (cai Q4 / CAI-1000 D5): a newly CREATEd table can inherit
--   default privileges — the EXACT leak class A3 detects. After apply, verify effective grants via
--   aclexplode/relacl (NOT information_schema.role_table_grants, which can green on what the role
--   cannot see). Expected: ZERO web-facing/PUBLIC grants. NB the leak lands on the NAMED roles
--   anon/authenticated (Supabase default privs), not PUBLIC — check all three. Query at the foot.

BEGIN;

CREATE TABLE IF NOT EXISTS public.invariant_assertion_runs (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invariant_ref text        NOT NULL REFERENCES public.invariant_registry(invariant_ref),
    run_at        timestamptz NOT NULL DEFAULT now(),   -- when the measurer ran (proof-of-run)
    outcome       text        NOT NULL CHECK (outcome IN ('PASS','FAIL','ERROR')),
    scope_checked text        NOT NULL,                 -- what the run examined (human-readable)
    finding_count integer     NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    evidence      jsonb,                                -- offending catalogue rows / the error; NEVER client rows
    run_by        text        NOT NULL,                 -- executing identity (measured-evidence author)
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- the count and the verdict cannot disagree: PASS is clean (0), FAIL carries findings (>0),
    -- ERROR found nothing because it could not complete (0). This makes a FAIL-with-no-evidence
    -- or a PASS-with-findings a WRITE-TIME crash, not a silent contradiction.
    CONSTRAINT invariant_assertion_runs_outcome_count_agree CHECK (
        (outcome='PASS'  AND finding_count = 0) OR
        (outcome='FAIL'  AND finding_count > 0) OR
        (outcome='ERROR' AND finding_count = 0)
    )
);

-- newest-run-per-invariant is the hot read (the live verdict); index it.
CREATE INDEX IF NOT EXISTS invariant_assertion_runs_ref_run_at_idx
    ON public.invariant_assertion_runs (invariant_ref, run_at DESC);

-- DENY-BY-DEFAULT GRANT HYGIENE (CAI-1018) — the exact leak class A3 detects, applied to our own
-- table. Supabase seeds ALTER DEFAULT PRIVILEGES granting PUBLIC/anon/authenticated/service_role on
-- EVERY new public table, so an additive `GRANT SELECT,INSERT` is a NO-OP on top of pre-granted ALL.
-- Two real holes were found on THIS table before a row was written, both this same class:
--   * anon SELECT + authenticated INSERT/UPDATE/DELETE  = web-readable AND web-writable (my catch;
--     a PUBLIC-only check greens on it — the grantee is a named role, not PUBLIC).
--   * service_role held UPDATE/DELETE/TRUNCATE           = the append-only immutable-log invariant
--     NOT enforced; a writer-as-service_role could erase FAIL proof-of-run (cc-storefront, CAI-1016).
-- The robust fix is the general form: REVOKE ALL from every non-owner role, then GRANT BACK exactly
-- the intended set — so no default-priv survives, present grantee or future role. CAI-1019: the
-- whitelist governs WHO (service_role belongs), append-only caps WHAT (SELECT+INSERT on this log).
REVOKE ALL ON public.invariant_assertion_runs FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON public.invariant_assertion_runs TO service_role;        -- append-only writer: immutable facts, NO UPDATE/DELETE/TRUNCATE (CAI-1016/1019)
GRANT SELECT           ON public.invariant_assertion_runs TO console_readonly;  -- auditor/console read (D7 substrate-readable)

COMMENT ON TABLE public.invariant_assertion_runs IS
  'Substrate-readable, joinable run log for automated invariant measurers (CAI-RESP-1014 / CAI-985). '
  'Join invariant_registry -> invariant_assertion_runs ON invariant_ref; the newest run_at row is the '
  'live PASS/FAIL/ERROR verdict + evidence. gate_status carries the not-green (COVERED=pass, pending=fail/error); '
  'this table carries the verdict + proof-of-run, so COVERED-on-fail is impossible AND a reader of gate_status '
  'is one join from the reason. Append-only: run rows are immutable historical facts.';

COMMIT;

-- ── POST-APPLY (run as the reviewer; CAI-1000 D5, aclexplode NOT information_schema) ──
-- Expect ZERO rows — no web-facing OR PUBLIC grant leaked onto the new table. NOTE (learned on
-- first apply): a PUBLIC-only check is INSUFFICIENT on Supabase — the default-priv leak lands on
-- the named roles anon/authenticated, not PUBLIC. Check all three grantees:
--   SELECT COALESCE(g.rolname,'PUBLIC') AS grantee, a.privilege_type
--     FROM pg_class c
--     CROSS JOIN LATERAL aclexplode(c.relacl) a
--     LEFT JOIN pg_roles g ON g.oid = a.grantee
--    WHERE c.relname = 'invariant_assertion_runs'
--      AND (g.rolname IS NULL OR g.rolname IN ('anon','authenticated'));   -- expect 0 rows
-- Effective grantee set must be EXACTLY: console_readonly(SELECT), postgres(owner), service_role
-- (SELECT,INSERT ONLY — append-only) — nothing web-facing, and service_role must NOT hold
-- UPDATE/DELETE/TRUNCATE. Exercise it (CAI-1016 re-audit): `SET LOCAL ROLE service_role; UPDATE ...`
-- must raise 'permission denied' (proof-of-run is immutable). Owner (postgres) can always mutate —
-- append-only against the owner is app-level discipline (the sink only INSERTs).
