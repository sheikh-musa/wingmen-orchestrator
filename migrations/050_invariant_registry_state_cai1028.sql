-- DRAFT (pending cai review + grant, CAI-1014/1028 pattern) — promote to migrations/050_... on grant.
-- invariant_registry_state — fix the FALSE 'NEVER' the view mints for a ran-and-failed invariant.
--
-- WHO/WHY: drafted by cc-fleet-health (A3 builder); cai stewards invariant_registry + this view and
--   REVIEWS + GRANTS (CAI-RESP-1028). Auditor = cc-storefront (it design-audited the CAI-1014 sink).
--
-- THE DEFECT (CAI-1028, Nazim observation): the view derived unexercised_kind from (gate_status,
--   last_asserted_at) ALONE. On a FAIL, the sink writes gate_status='pending' + last_asserted_at
--   UNCHANGED (NULL) by design (CAI-1014). So the view's `WHEN last_asserted_at IS NULL THEN 'NEVER'`
--   branch MINTED 'NEVER' for RESIDENCY-1 — which RAN and FAILED (a real invariant_assertion_runs
--   row, run_at set). That is a false finding: the raw row (pending, NULL) is honestly AMBIGUOUS, but
--   the VIEW asserted 'NEVER' it cannot support. (My earlier lean — stamp last_asserted_at on FAIL —
--   was REJECTED: it is not gate_status-gated for unexercised_kind and would flip 'NEVER' ->
--   'MANUALLY-ATTESTED', the view claiming a human attested an automated failure. CAI-1028.)
--
-- THE FIX (view-only, no writer change, no new column, no gate_status vocab change): LEFT JOIN the
--   LATEST invariant_assertion_runs per invariant_ref and let the NOT-GREEN sub-classification use it,
--   so it never emits 'NEVER' when a run exists. This USES the run-record CAI-1014 already named as
--   the joinable evidence — it COMPLETES CAI-1014, it does not reverse it.
--
-- BINDING (CAI-1028): is_exercised_fresh MUST stay gated on gate_status='COVERED' + fresh — the join
--   may ONLY enrich the not-green taxonomy, NEVER widen green. Green stays STRUCTURAL (CAI-1014).
--   Verified below (rolled-back) that the is_exercised_fresh (green) count is unchanged.
--
-- VERIFIED (cc-fleet-health, rolled-back txn on live data 2026-08-17):
--   RESIDENCY-1 unexercised_kind: 'NEVER' -> 'MEASURED-FAILING' (the fix).
--   is_exercised_fresh green count: unchanged (0 -> 0). exercise_state + is_exercised_fresh untouched.
--   MANUALLY-ATTESTED rows (MIGRATION-1, REPORT-IMMUT-1, ...) intact — the MANUAL signal preserved.
--   Only RESIDENCY-1 changed (the only invariant with a run record); the other 33 unchanged.
--
-- cc-storefront TO EXERCISE (CAI-1028): on a TEST invariant_ref, record a synthetic FAIL run and a
--   synthetic PASS run and show the view labels each correctly (MEASURED-FAILING vs the green path)
--   AND is_exercised_fresh stays FALSE for the FAIL. Rolled back.

CREATE OR REPLACE VIEW invariant_registry_state AS
 SELECT r.invariant_ref, r.domain, r.statement, r.gate_ref, r.gate_status, r.severity,
    r.origin_incident, r.last_asserted_at, r.stewarded_by, r.seeded_by, r.created_at, r.updated_at,
        CASE
            WHEN NOT t.measurer_live THEN 'UNEXERCISED'::text
            WHEN r.last_asserted_at IS NULL THEN 'UNEXERCISED'::text
            WHEN r.last_asserted_at < t.fresh_since THEN 'STALE'::text
            ELSE 'EXERCISED'::text
        END AS exercise_state,
    -- UNCHANGED (BINDING): green stays structural, gate_status='COVERED'-gated.
    t.measurer_live AND r.last_asserted_at IS NOT NULL AND r.last_asserted_at >= t.fresh_since AS is_exercised_fresh,
        CASE WHEN r.last_asserted_at IS NULL THEN NULL::interval
             ELSE date_trunc('second', now() - r.last_asserted_at) END AS since_last_assertion,
    -- CAI-1028: not-green sub-classification now uses the latest run record.
        CASE
            WHEN t.measurer_live AND r.last_asserted_at IS NULL THEN 'DECLARED-SILENT'::text
            WHEN t.measurer_live THEN NULL::text
            WHEN run.outcome IN ('FAIL','ERROR') THEN 'MEASURED-FAILING'::text
            WHEN run.outcome = 'PASS' THEN 'MEASURED-PASSING'::text   -- defensive: a PASS should be COVERED/measurer_live
            WHEN r.last_asserted_at IS NULL THEN 'NEVER'::text        -- no run AND no attestation = genuinely never
            ELSE 'MANUALLY-ATTESTED'::text                            -- last_asserted_at set, no run = human attestation
        END AS unexercised_kind
   FROM invariant_registry r
     CROSS JOIN LATERAL (SELECT now() - '30 days'::interval AS fresh_since,
            r.gate_status IS NOT NULL AND upper(r.gate_status) = 'COVERED' AS measurer_live) t
     LEFT JOIN LATERAL (SELECT outcome, run_at FROM invariant_assertion_runs ar
            WHERE ar.invariant_ref = r.invariant_ref ORDER BY ar.run_at DESC LIMIT 1) run ON true;
