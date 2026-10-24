-- 118_operator_asks_needs_musa.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Needs-Musa flag + plain-language operator_summary (console-revamp lane,
-- orch-console bus #75387 blocker + #75355 proposal GO-implicit-in-#75387).
--
-- WHY: the live asks board (op#33598, /asks) flags "Needs Musa" rows, but the
-- 2026-10-25 reconcile (reports/asks-reconcile-20261025/report.md) proved the
-- 24-item "genuinely needs Musa's own decision/action" shortlist is NOT
-- derivable from any existing structured column: only 4/24 rows have
-- waiting_on_operator=true, all 24 have delegated_to owners (lanes, not
-- musa), 23/24 have chase_by, and waiting_on_operator is true on only 7 of
-- all 282 open asks. The judgment lived ONLY in the report's prose — so the
-- board could not show it live and the shortlist would silently rot the
-- moment the report went stale.
--
-- WHAT THIS ADDS:
--  1. operator_asks.needs_operator_decision boolean NOT NULL DEFAULT false —
--     the structured "this ask is gated on Musa's own decision/action" mark,
--     orthogonal to waiting_on_operator (thread-derived waiting state) and to
--     triage_state ("is this text even an ask"). Maintained going forward via
--     scripts/asks_triage.py --needs-musa on|off (tri-state: absent = leave
--     untouched, so a plain re-triage never silently drops the mark).
--  2. operator_asks.operator_summary text — the plain-language, decision-
--     first line the console Needs-Musa HERO renders instead of the internal
--     triage_summary. orch-console #75387: the hero is the one Musa-facing
--     surface on that page; internal saga text / lane code-names defeat the
--     board's purpose (it exists BECAUSE he wanted his asks VISIBLE and
--     PLAIN). Other tabs keep triage_summary — those are for the fleet.
--
-- SEED: the 24 shortlist ids from the 2026-10-25 reconcile, one plain-language
-- line each (decision-first, Musa's own product vocabulary, no agent
-- code-names/hc#/op# refs). UPDATEs are idempotent; ids are operator_asks ids
-- (a plain UPDATE by id — never an explicit id into an identity insert).
--
-- Grants: table-level SELECT (console_readonly, service_role) already covers
-- future columns, but the fleet norm (column GRANT on new columns) is made
-- explicit here so the board's read path never depends on that subtlety.
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.operator_asks
  ADD COLUMN IF NOT EXISTS needs_operator_decision boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS operator_summary text;

COMMENT ON COLUMN public.operator_asks.needs_operator_decision IS
  'TRUE = this open ask is gated on Musa''s own decision/action (the 2026-10-25 '
  'reconcile 24-item shortlist shape). NOT derivable from waiting_on_operator '
  '(only 4/24 shortlist rows had it) — it is a triage judgment, set/unset via '
  'scripts/asks_triage.py --needs-musa on|off. The console /asks hero reads it '
  'live (bus #75387).';
COMMENT ON COLUMN public.operator_asks.operator_summary IS
  'Plain-language, decision-first line for the Musa-facing Needs-Musa hero '
  '(what HE must decide, his own product vocabulary, no agent code-names). '
  'NULL = hero falls back to the sanitized triage_summary text. Written via '
  'asks_triage.py --musa-summary.';

-- Explicit column grants (belt-and-suspenders vs the table-level SELECT;
-- mirrors the committed_date/triage_summary grant shape).
GRANT SELECT (needs_operator_decision, operator_summary)
  ON public.operator_asks TO console_readonly;
GRANT SELECT, INSERT, UPDATE, REFERENCES (needs_operator_decision, operator_summary)
  ON public.operator_asks TO service_role;

-- ── one-time seed: the 2026-10-25 reconcile shortlist (24 ids) ──────────────
UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide what the events dashboard should track — your answers fold into the re-platform scope.'
WHERE id = 2141;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide the required-core scope for the cosem platform re-platform (you paused it 21 Oct) — the build lane is waiting on this call.'
WHERE id = 4674;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Approve folding ORBAT into the Trainee Overview (list/tiles toggle) and choose admin-only vs trainer-scoped generation.'
WHERE id = 3650;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Pick the "edited after publish" warning style: Option A (amber badge) or Option B (banner + per-block tag).'
WHERE id = 4743;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide the logs cleanup scope: retire all 7 log streams or keep the big 3 (asks, bus, commitments).'
WHERE id = 4494;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Approve the second-brain vault build shape before any build starts.'
WHERE id = 4499;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Choose the break-glass console secrets source: Google Secret Manager or an offline bundle.'
WHERE id = 4511;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Choose the residency-proof audience and bar: school-reassuring or regulator-grade.'
WHERE id = 4215;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Sign up for the Oracle always-free ARM cloud — needs your email, phone and card.'
WHERE id = 3732;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Confirm which Google account should own the OEH Search Console / SEO (OEH is currently dormant).'
WHERE id = 1146;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Give the gist and recipient for the MUIS CEO email so it can finally be sent.'
WHERE id = 3519;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide whether to terminate the z.ai (GLM) plan before its 6 Nov renewal — reminder is due to you ~4 Nov.'
WHERE id = 3447;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Confirm the peak-hours policy for GLM lanes: pause them daily or move the work — the automation is on hold pending this.'
WHERE id = 3600;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Confirm the zone-sharing model for pumpers — how Zone 6 and Zone 2 should be treated.'
WHERE id = 4604;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'The unclaimed-trainers panel (33 records) is live: archive them yourself, or approve a bulk archive.'
WHERE id = 4811;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide on the duplicate Arqam in Group 3: keep the new link or undo it — then the ghost record can be archived.'
WHERE id = 4813;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide how excused/cancelled lesson days (the hazmat-dept mass absence) should count in attendance deficits.'
WHERE id = 4690;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Decide who may edit the schedule rules — your earlier message on this was cut off.'
WHERE id = 4358;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Go/no-go on cleaning up the Artifact Registry to cut the cosem GCP cost.'
WHERE id = 4701;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Confirm timing + the continuity gate for moving the console and lanes from the Mac Mini to the new VPS.'
WHERE id = 4506;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Reseat the external drive (data cable / direct port / power) and re-check — it is still not being detected.'
WHERE id = 4514;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Give the explicit go for the Paperclip root install on the partner VPS — unless your 23 Oct go already covers it, in which case say so.'
WHERE id = 3956;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Approve weeks 4+ for everyone to see — weeks 1-3 are live; your approval per week is the gate.'
WHERE id = 4151;

UPDATE public.operator_asks SET
  needs_operator_decision = true,
  operator_summary = 'Review and approve the Claude vs Gemini cost brief before it goes to your managers.'
WHERE id = 3803;

COMMIT;
