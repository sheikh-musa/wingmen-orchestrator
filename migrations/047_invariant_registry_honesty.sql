-- 047_invariant_registry_honesty.sql
-- CAI-RESP-986 §1+§2 — the immediate, no-build honesty fix for `invariant_registry`.
--
-- WHY (measured 2026-08-16, orch-console, verified at source):
--   invariant_registry holds 34 named invariants. 28 have last_asserted_at NULL, and the NULL set
--   is the load-bearing one — MONEY-1..7, RESIDENCY-1..4, AUTHORITY-1, DEPLOY-1, TOKENS-1,
--   SECRET-HYGIENE-1, SCHEMA-1, LAYER-VOCAB-001, GATE-POSTURE-001, EXEC-1..5. The 6 non-NULL are
--   25-31 days stale. gate_status is 32 MANUAL + 2 pending — ZERO automated. A repo-wide grep of
--   the orchestrator found NO code that writes this table and NO code that reads it.
--   A NULL timestamp sitting beside "MONEY-1: No anon/authenticated table-write on money tables"
--   reads to any human or board as COVERAGE. It is the opposite: it is a gap that has never once
--   been exercised. That lie is the exact defect CAI-978 named, living in the governance table.
--
-- THE DOCTRINE THIS ENCODES (CAI-RESP-986 §1, binding fleet-wide — the general form of CAI-978):
--   NEVER GREEN ON ABSENCE-OF-SIGNAL. A row is satisfied ONLY if its signal is FRESH *and* its
--   measurer is PROVABLY LIVE. Missing signal, stale signal, or a dead/absent measurer => amber
--   (UNEXERCISED). This is why the measurer check DOMINATES the timestamp: a fresh timestamp
--   written by a measurer that no longer exists is not evidence, and a manual assertion is not a
--   live measurer. cai CONFIRMED this strict reading and directed that it NOT be loosened —
--   "a manual assertion is a CLAIM ('a human says MONEY-1 holds'), not EXERCISED evidence (a
--   measurer ran and observed it hold)". PII-ANON-1 therefore reads UNEXERCISED despite holding a
--   real 2026-07-22 timestamp. That is the rule working, not a bug. Today all 34 rows read
--   UNEXERCISED — which is the truth.
--
-- CAI-RESP-986 §2 explicitly REJECTED deleting this table: the invariants are real and cited
--   across the fleet, and db/ceayj-tenant/30_substrate_decommission.sql names invariant_registry
--   as one of the three things the coordination plane KEEPS. It stays a DECLARATIVE catalogue.
--   §3: MEASURED is the north star but PHASED — wire ONE invariant end-to-end first (the CAI-985
--   A3 automation, which exercises a RESIDENCY-class invariant and can write RESIDENCY-1), then
--   generalise. A hand-maintained coverage list would BE the failure; that is what killed
--   lane_tasks #60.
--
-- ADDITIVE AND REVERSIBLE. Creates one view; alters no data, no columns, no constraints.
--
-- APPLY: scripts/apply_migration.py 047 --silo tscuymavysscrvoberrr (historical
-- applier: apply_invariant_registry_honesty.py, deleted 2026-09-05 PR #89).
--   NEVER `supabase db push` (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001: the CLI shadow-diff
--   path re-applies historic CREATE OR REPLACE VIEW bodies and silently strips later arms).
--   Target: the SUBSTRATE db (coordination plane), NOT any client silo.
--
-- ── cc-quality review of PR #74 (report reports/backend-review/pr74-invariant-registry-honesty.md)
--    F1 (MED) FIXED: the 30-day window and the live-measurer allowlist were each DUPLICATED across
--      arms. That duplication was the one mechanism that could break the proven agreement between
--      `exercise_state` and `is_exercised_fresh` — a maintainer tuning one arm and not the other
--      reintroduces exactly the boolean-vs-label disagreement this view must never have. Both are
--      now SINGLE-SOURCED in the LATERAL below and referenced everywhere.
--    F3 (LOW-MED) FIXED: `r.*` is gone. It was confirmed empirically that `r.*` + a future
--      ALTER TABLE ADD COLUMN makes this migration UN-RE-APPLIABLE — the new base column re-expands
--      into the MIDDLE of the output list and CREATE OR REPLACE VIEW is append-only, so it throws
--      "cannot change name of view column". Base columns are enumerated explicitly instead.
--    F4 (LOW) — PREMISE WAS WRONG, AND SO WAS MINE, AND MY TEST IS WHAT CAUGHT IT.
--      cc-quality proposed hardening `gate_status` with a CHECK because it is "unconstrained free
--      text". It is NOT. `invariant_registry_gate_status_check` already exists and allows exactly
--      ARRAY['COVERED','MANUAL','pending'].
--      The worse half is mine: the first version of this view used an INVENTED allowlist
--      (MEASURED/AUTO/AUTOMATED) that I never checked against the schema. NOT ONE of those tokens
--      is writable — so `EXERCISED` was UNREACHABLE, the view could never have gone green no
--      matter what a sink did, and the instruction I had already sent cc-fleet-health ("set
--      gate_status='MEASURED'") would have failed the CHECK at write time. Found by running the
--      F2 simulated-sink test rather than by reading, which is the whole argument for the test.
--      The live-measurer token is now the schema's own vocabulary: COVERED.
--      NOTE THE COUPLING: this allowlist is bounded by that CHECK constraint. Widening one
--      without the other silently breaks the sink path — change both together, and the constraint
--      is a governance artifact cai stewards, so widening it is HER call, not this migration's.

CREATE OR REPLACE VIEW invariant_registry_state AS
SELECT
    -- Base columns enumerated explicitly (F3): never `r.*`, so that a future ADD COLUMN on
    -- invariant_registry cannot make this view un-re-appliable.
    r.invariant_ref,
    r.domain,
    r.statement,
    r.gate_ref,
    r.gate_status,
    r.severity,
    r.origin_incident,
    r.last_asserted_at,
    r.stewarded_by,
    r.seeded_by,
    r.created_at,
    r.updated_at,
    CASE
        -- No live measurer => UNEXERCISED regardless of any timestamp present. Doctrine first.
        WHEN NOT t.measurer_live THEN 'UNEXERCISED'
        -- Live measurer, but it has never written => still never exercised.
        WHEN r.last_asserted_at IS NULL THEN 'UNEXERCISED'
        -- Live measurer that has gone quiet: a stale assertion is not current evidence.
        WHEN r.last_asserted_at < t.fresh_since THEN 'STALE'
        ELSE 'EXERCISED'
    END AS exercise_state,
    -- Explicit boolean so a board cannot accidentally render a non-EXERCISED state as green by
    -- string-comparing wrong. Green requires an affirmative TRUE here, never absence of a flag.
    -- Built from the SAME single-sourced t.* terms as the label above, so the two cannot drift
    -- apart under maintenance (cc-quality proved they agree today across a 54-cell matrix; F1 is
    -- what keeps that true tomorrow).
    (
        t.measurer_live
        AND r.last_asserted_at IS NOT NULL
        AND r.last_asserted_at >= t.fresh_since
    ) AS is_exercised_fresh,
    CASE
        WHEN r.last_asserted_at IS NULL THEN NULL
        ELSE date_trunc('second', now() - r.last_asserted_at)
    END AS since_last_assertion,
    -- CAI-RESP-986 follow-up (cai's optional refinement, taken): a SEQUENCING signal only.
    -- The green/amber binary above is unchanged and stays strict -- both kinds below are
    -- correctly NOT green. This exists so that whoever wires sinks can prioritise: an invariant
    -- no human has ever attested is a colder start than one someone eyeballed recently.
    -- Deliberately a SEPARATE column rather than new exercise_state values, so that any reader
    -- matching exercise_state = 'UNEXERCISED' keeps working unchanged.
    -- F5 (cc-quality supplement): 'DECLARED-SILENT' must come FIRST. A measurer that is
    -- DECLARED but has never written is the single most sequencing-urgent case there is —
    -- it is the pipeline_clock failure exactly (bug_pipeline_readiness read 10/10 green for
    -- 39 days because its measurer was never scheduled). Before this arm existed it fell into
    -- a NULL bucket that was neither NEVER nor MANUALLY-ATTESTED, so a GROUP BY over
    -- UNEXERCISED rows would silently drop the very rows that most need attention.
    CASE
        WHEN t.measurer_live AND r.last_asserted_at IS NULL THEN 'DECLARED-SILENT'
        -- Live measurer that HAS written: the row is STALE or EXERCISED, so "what kind of
        -- unexercised" is genuinely not the question being asked.
        WHEN t.measurer_live THEN NULL
        WHEN r.last_asserted_at IS NULL THEN 'NEVER'
        ELSE 'MANUALLY-ATTESTED'
    END AS unexercised_kind
FROM invariant_registry r
-- F1: the freshness window and the definition of "a live measurer" are defined ONCE, here, and
-- referenced by every arm above. Changing the window or adding an automated gate token is a
-- one-place edit; it is structurally impossible for the label and the boolean to be tuned apart.
CROSS JOIN LATERAL (
    SELECT
        now() - interval '30 days' AS fresh_since,
        (
            r.gate_status IS NOT NULL
            AND upper(r.gate_status) = 'COVERED'
        ) AS measurer_live
) t;

COMMENT ON VIEW invariant_registry_state IS
    'CAI-RESP-986: honest read of invariant_registry. NEVER GREEN ON ABSENCE-OF-SIGNAL -- a row is '
    'EXERCISED only if a LIVE measurer (gate_status COVERED) wrote last_asserted_at '
    'within 30 days. NULL, stale, or manual/absent-measurer all read UNEXERCISED/STALE, never '
    'coverage. Read THIS, not the base table, anywhere a human or board judges invariant status. '
    'As of 2026-08-16 all 34 rows read UNEXERCISED (no sink exists yet) -- that is the truth, not a bug.';

COMMENT ON COLUMN invariant_registry.last_asserted_at IS
    'NULL means NEVER ASSERTED -- a gap, NOT coverage (CAI-RESP-986). Nothing writes this column '
    'today; the first sink is the CAI-985 A3 automation writing RESIDENCY-1 (CAI-RESP-986 s3, phased, '
    'one invariant first). A sink MUST also set gate_status=''COVERED'' (the only automated-gate token '
    'the CHECK constraint allows) or the row stays UNEXERCISED by design. Do NOT hand-maintain this column -- a hand-kept coverage list is the '
    'failure that killed lane_tasks #60. Judge status via invariant_registry_state, never this raw.';
