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
--   (UNEXERCISED). This is why gate_status is checked FIRST below and why it dominates: a fresh
--   timestamp written by a measurer that no longer exists is not evidence, and a manual assertion
--   is not a live measurer. Today that means all 34 rows read UNEXERCISED — which is the truth.
--
-- CAI-RESP-986 §2 explicitly REJECTED deleting this table: the invariants are real and cited
--   across the fleet, and db/ceayj-tenant/30_substrate_decommission.sql names invariant_registry
--   as one of the three things the coordination plane KEEPS. It stays a DECLARATIVE catalogue.
--   §3: MEASURED is the north star but PHASED — wire ONE invariant end-to-end first (the CAI-985
--   A3 automation, which exercises a RESIDENCY-class invariant and can write RESIDENCY-1), then
--   generalise. A hand-maintained coverage list would BE the failure; that is what killed
--   lane_tasks #60.
--
-- ADDITIVE AND REVERSIBLE. Creates one view; alters no data, no columns, no constraints. Existing
--   readers/writers of the base table are unaffected (there are none today, but that is the point:
--   whatever reads it NEXT gets the truth by default rather than having to remember to derive it).
--
-- APPLY: direct psycopg only — scripts/apply_invariant_registry_honesty.py.
--   NEVER `supabase db push` (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001: the CLI shadow-diff
--   path re-applies historic CREATE OR REPLACE VIEW bodies and silently strips later arms).
--   Target: the SUBSTRATE db (coordination plane), NOT any client silo.

-- A measurer counts as LIVE only if the registry says the gate is automated. Anything else --
-- MANUAL, pending, NULL -- means a human remembered (or did not), which is not a measurer.
-- Kept as a list rather than inlined so that wiring the first real sink is a one-word change here.
CREATE OR REPLACE VIEW invariant_registry_state AS
SELECT
    r.*,
    CASE
        -- No live measurer => UNEXERCISED regardless of any timestamp present. Doctrine first.
        WHEN r.gate_status IS NULL
          OR upper(r.gate_status) NOT IN ('MEASURED', 'AUTO', 'AUTOMATED')
            THEN 'UNEXERCISED'
        -- Live measurer, but it has never written => still never exercised.
        WHEN r.last_asserted_at IS NULL
            THEN 'UNEXERCISED'
        -- Live measurer that has gone quiet: a stale assertion is not current evidence.
        WHEN r.last_asserted_at < now() - interval '30 days'
            THEN 'STALE'
        ELSE 'EXERCISED'
    END AS exercise_state,
    -- Explicit boolean so a board cannot accidentally render a non-EXERCISED state as green by
    -- string-comparing wrong. Green requires an affirmative TRUE here, never absence of a flag.
    (
        r.gate_status IS NOT NULL
        AND upper(r.gate_status) IN ('MEASURED', 'AUTO', 'AUTOMATED')
        AND r.last_asserted_at IS NOT NULL
        AND r.last_asserted_at >= now() - interval '30 days'
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
    CASE
        WHEN r.gate_status IS NOT NULL
         AND upper(r.gate_status) IN ('MEASURED', 'AUTO', 'AUTOMATED')
            THEN NULL  -- live measurer: not an unexercised-kind question at all
        WHEN r.last_asserted_at IS NULL
            THEN 'NEVER'
        ELSE 'MANUALLY-ATTESTED'
    END AS unexercised_kind
FROM invariant_registry r;

COMMENT ON VIEW invariant_registry_state IS
    'CAI-RESP-986: honest read of invariant_registry. NEVER GREEN ON ABSENCE-OF-SIGNAL -- a row is '
    'EXERCISED only if a LIVE measurer (gate_status MEASURED/AUTO/AUTOMATED) wrote last_asserted_at '
    'within 30 days. NULL, stale, or manual/absent-measurer all read UNEXERCISED/STALE, never '
    'coverage. Read THIS, not the base table, anywhere a human or board judges invariant status. '
    'As of 2026-08-16 all 34 rows read UNEXERCISED (no sink exists yet) -- that is the truth, not a bug.';

COMMENT ON COLUMN invariant_registry.last_asserted_at IS
    'NULL means NEVER ASSERTED -- a gap, NOT coverage (CAI-RESP-986). Nothing writes this column '
    'today; the first sink is the CAI-985 A3 automation writing RESIDENCY-1 (CAI-RESP-986 s3, phased, '
    'one invariant first). Do NOT hand-maintain it -- a hand-kept coverage list is the failure that '
    'killed lane_tasks #60. Judge status via the invariant_registry_state view, never this column raw.';
