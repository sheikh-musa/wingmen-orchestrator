-- 082_operator_asks_triage.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Adds a TRIAGE axis to operator_asks (bus #45552 orch-console problem
-- report -> #45555 cc-substrate design proposal -> #45557 orch-console GO,
-- 5 binding conditions, all addressed below and in the accompanying code
-- changes in this same PR).
--
-- ROOT CAUSE: maybe_track_ask() (nervous_system/operator_log.py) opens an
-- operator_asks row for EVERY inbound message on a tracked surface, and
-- nothing has ever classified it since. asks_daily_digest.py's
-- fetch_open_asks() selects every closed_at IS NULL row and prints its raw
-- `ask` text verbatim, so a bare ack ("ok", "thanks") reads exactly like a
-- real ask. 152 accumulated open rows, 82 of them never triaged at all
-- (op#23228: "bruh not every message is a request?").
--
-- WHY STORED, NOT DERIVED (departure from migration 044's "status is never
-- stored" invariant -- flagging explicitly since that invariant is load-
-- bearing doctrine, and orch-console explicitly agreed with this reasoning
-- in #45557): that invariant covers ask PROGRESS (open/in_progress/done),
-- which is mechanically derivable from the linked agent_messages thread.
-- TRIAGE -- "is this text even an ask" -- is a judgment call, not
-- derivable from the thread, so it has to be recorded somewhere. It is an
-- orthogonal axis, not a violation: closed_at/closed_reason (the existing
-- lifecycle) behaves exactly as it does today for triage_state='ask' rows.
--
-- SCHEMA CHANGE:
--   triage_state      text NOT NULL DEFAULT 'captured'
--                      CHECK (triage_state IN ('captured','ask','not_an_ask','done'))
--   triage_summary     text  -- the ONLY thing the digest is allowed to show;
--                             required in practice for triage_state='ask'
--   triage_evidence_ref text -- optional pointer (bus/op ids) for triage_state='done'
--   triaged_at         timestamptz
--   triaged_by         text  -- agent_id, or 'heuristic' for the pre-classifier
--                             in operator_log.py
--
-- BACK-COMPAT: DEFAULT 'captured' means every pre-existing row not touched
-- by the backfill below lands there automatically -- no row is silently
-- reclassified by this migration; the backfill below is an explicit,
-- reviewed, per-id decision, not a blanket default.
--
-- BACKFILL (bus #45557 condition 1): orch-console's hand-triage pass over
-- the pre-existing ledger, from two source files
-- (scratchpad/asks_triage.tsv + asks_triage_assigned.tsv, 155 rows total,
-- disjoint ask_ids, confirmed via `comm -12` on the id columns):
--   - 23 rows -> triage_state='not_an_ask', closed (bare acks / approvals /
--     questions answered inline -- class A in the source TSVs).
--   - 100 rows -> triage_state='done', closed (delivered/built work,
--     including 4 rows that are exact duplicates of another still-open ask
--     -- closed_reason='duplicate' for those 4: ids 176/214/216/262,
--     "Duplicate of NNN" in the source short_reason -- class B, plus the
--     4 duplicate class-C rows).
--   - 32 rows -> triage_state='ask', stays open, with a clean one-line
--     triage_summary written by cc-substrate from the source short_reason
--     (a reader understands it without context -- the digest's actual
--     quality bar) -- class C non-duplicate rows.
-- NOTE (honest discrepancy, not silently forced to match): orch-console's
-- own bus #45557 prose said "152 -> 32 open; 69 done + 23 not-an-ask + 4
-- duplicates closed". The 32-open and 23-not_an_ask figures verify EXACTLY
-- against the source TSVs; the "69 done" does not (actual done count incl.
-- duplicates is 100) and the total is 155 rows, not 152. The row-level TSV
-- data (the explicitly-named source: "the summaries are in my triage
-- files") is treated as ground truth here rather than forcing a match to
-- the prose; flagged to orch-console in the accompanying bus report.
--
-- Each backfill UPDATE is guarded WHERE ... AND closed_at IS NULL, same
-- shape as asks_close.py, so a row already closed by some other path
-- between the TSV pass and this apply is left untouched rather than
-- silently overwritten.
--
-- REVERT:
--   ALTER TABLE public.operator_asks
--     DROP COLUMN IF EXISTS triage_state,
--     DROP COLUMN IF EXISTS triage_summary,
--     DROP COLUMN IF EXISTS triage_evidence_ref,
--     DROP COLUMN IF EXISTS triaged_at,
--     DROP COLUMN IF EXISTS triaged_by;
--   -- the backfill's closed_at/closed_reason changes on the 123 closed rows
--   -- are NOT reverted by this -- they were already-real closes (done/
--   -- not-an-ask/duplicate), not new lifecycle state; only the triage
--   -- columns themselves are new.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.operator_asks
  ADD COLUMN IF NOT EXISTS triage_state text NOT NULL DEFAULT 'captured',
  ADD COLUMN IF NOT EXISTS triage_summary text,
  ADD COLUMN IF NOT EXISTS triage_evidence_ref text,
  ADD COLUMN IF NOT EXISTS triaged_at timestamptz,
  ADD COLUMN IF NOT EXISTS triaged_by text;

ALTER TABLE public.operator_asks
  ADD CONSTRAINT operator_asks_triage_state_chk
  CHECK (triage_state IN ('captured', 'ask', 'not_an_ask', 'done'));

COMMENT ON COLUMN public.operator_asks.triage_state IS
  'Orthogonal to closed_at/closed_reason (progress, mechanically derived '
  'elsewhere is NOT what this is): captured = not yet judged, ask = a real '
  'request (triage_summary is what the digest shows), not_an_ask = bare '
  'ack/no request, done = a real request that has since been delivered. '
  'See migration 082 header for the full rationale.';
COMMENT ON COLUMN public.operator_asks.triage_summary IS
  'The ONLY text the digest is allowed to render for a triage_state=''ask'' '
  'row -- never the raw `ask` column. A clean one-liner, readable without '
  'context.';
COMMENT ON COLUMN public.operator_asks.triage_evidence_ref IS
  'Optional pointer (bus/op ids, comma-separated) to where a triage_state='
  '''done'' row was actually delivered. Not a foreign key.';
COMMENT ON COLUMN public.operator_asks.triaged_at IS 'When triage_state was last set.';
COMMENT ON COLUMN public.operator_asks.triaged_by IS
  'agent_id that set triage_state, or the literal ''heuristic'' for the '
  'automatic bare-ack pre-classifier in operator_log.py.';

CREATE INDEX IF NOT EXISTS operator_asks_triage_ask_idx
  ON public.operator_asks (created_at)
  WHERE triage_state = 'ask' AND closed_at IS NULL;

CREATE INDEX IF NOT EXISTS operator_asks_triage_captured_idx
  ON public.operator_asks (created_at)
  WHERE triage_state = 'captured' AND closed_at IS NULL;

-- ---- BACKFILL: orch-console's hand-triage pass (155 rows) ----

-- not_an_ask (bare acks / approvals / inline-answered questions)
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Approval ("yes proceed") to BAPT 3-round proposal', triaged_at=now(), triaged_by='orch-console' WHERE id=139 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Answer to clarifying question (all Rescue to night)', triaged_at=now(), triaged_by='orch-console' WHERE id=141 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Decision/ack ("17 weeks it is")', triaged_at=now(), triaged_by='orch-console' WHERE id=157 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered in same exchange', triaged_at=now(), triaged_by='orch-console' WHERE id=159 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered in same exchange (save then Documents tab)', triaged_at=now(), triaged_by='orch-console' WHERE id=160 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='why the delay question answered', triaged_at=now(), triaged_by='orch-console' WHERE id=173 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered; the actual request is ask #177', triaged_at=now(), triaged_by='orch-console' WHERE id=174 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered', triaged_at=now(), triaged_by='orch-console' WHERE id=178 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered in same exchange', triaged_at=now(), triaged_by='orch-console' WHERE id=180 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Approval ("ok") to password rotation', triaged_at=now(), triaged_by='orch-console' WHERE id=194 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Superseded by op#23133 (opportunistic BAPT)', triaged_at=now(), triaged_by='orch-console' WHERE id=227 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered', triaged_at=now(), triaged_by='orch-console' WHERE id=230 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered', triaged_at=now(), triaged_by='orch-console' WHERE id=231 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Urgency remark; files delivered', triaged_at=now(), triaged_by='orch-console' WHERE id=232 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Urgency remark ("full tilt")', triaged_at=now(), triaged_by='orch-console' WHERE id=233 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered (tmux name)', triaged_at=now(), triaged_by='orch-console' WHERE id=234 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Confirmation/decision, acknowledged', triaged_at=now(), triaged_by='orch-console' WHERE id=235 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Constraint statement, acknowledged', triaged_at=now(), triaged_by='orch-console' WHERE id=236 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered', triaged_at=now(), triaged_by='orch-console' WHERE id=237 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='ETA ping; answered and files delivered', triaged_at=now(), triaged_by='orch-console' WHERE id=245 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Urgency remark', triaged_at=now(), triaged_by='orch-console' WHERE id=246 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Status ping answered', triaged_at=now(), triaged_by='orch-console' WHERE id=249 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='not_an_ask', closed_at=now(), closed_reason='not_a_request', triage_summary='Question answered / wording correction', triaged_at=now(), triaged_by='orch-console' WHERE id=255 AND closed_at IS NULL;

-- done (delivered/built work, incl. exact duplicates of another open ask)
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Column A fixed + colour coding delivered (interim file, then formatting-fixed Overview)', triage_evidence_ref='op#22929,op#23082', triaged_at=now(), triaged_by='orch-console' WHERE id=129 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='BAPT packed/1 group per day and SCBA 30/15 built in; new schedule delivered', triage_evidence_ref='op#22950,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=131 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Date window from period dates live; superseded by 17-week decision (op#22985)', triage_evidence_ref='op#22977,op#22987', triaged_at=now(), triaged_by='orch-console' WHERE id=132 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Theory-test cap + HAZMAT day label taken into scheduler rules; files delivered', triage_evidence_ref='op#22924,op#22950,op#23074', triaged_at=now(), triaged_by='orch-console' WHERE id=133 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='All generated files use the updated dates', triage_evidence_ref='op#23074,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=134 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Rescue/Pump Operation moved to night, schedule re-run', triage_evidence_ref='op#22948,op#22950', triaged_at=now(), triaged_by='orch-console' WHERE id=135 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Answer to draft-period question; rename/delete of drafts shipped', triage_evidence_ref='op#22978', triaged_at=now(), triaged_by='orch-console' WHERE id=137 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Outstanding Fees fixed after full-size test; answered', triage_evidence_ref='op#22969,op#22983', triaged_at=now(), triaged_by='orch-console' WHERE id=147 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Question answered (13 weeks does not fit, bottleneck explained)', triage_evidence_ref='op#22976', triaged_at=now(), triaged_by='orch-console' WHERE id=150 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Minimum weeks measured and reported (17)', triage_evidence_ref='op#22981,op#22984', triaged_at=now(), triaged_by='orch-console' WHERE id=154 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='ESB built from saved schedule and sent', triage_evidence_ref='op#23014', triaged_at=now(), triaged_by='orch-console' WHERE id=165 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='All 3 files delivered same day', triage_evidence_ref='op#23014,op#23052', triaged_at=now(), triaged_by='orch-console' WHERE id=170 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Hermes studied; fleet_lessons table built and applied', triage_evidence_ref='op#23019,am#44889', triaged_at=now(), triaged_by='orch-console' WHERE id=179 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Retests split onto separate days, applied and files resent', triage_evidence_ref='op#23074,am#45047', triaged_at=now(), triaged_by='orch-console' WHERE id=182 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='1 Jan treated as closed, applied', triage_evidence_ref='op#23074,am#45047', triaged_at=now(), triaged_by='orch-console' WHERE id=184 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Files located/re-sent', triage_evidence_ref='op#23052', triaged_at=now(), triaged_by='orch-console' WHERE id=189 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Rotation done overnight and verified closed', triage_evidence_ref='am#45384,op#23180', triaged_at=now(), triaged_by='orch-console' WHERE id=195 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Buffer-week savings measured and answered (0 weeks)', triage_evidence_ref='op#23087', triaged_at=now(), triaged_by='orch-console' WHERE id=196 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='cosem lanes moved to Syed pool', triage_evidence_ref='am#44884,am#45354', triaged_at=now(), triaged_by='orch-console' WHERE id=198 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Formatting fixed in file and in app generator', triage_evidence_ref='op#23082,op#23090', triaged_at=now(), triaged_by='orch-console' WHERE id=200 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Fonts fixed; spare days explained', triage_evidence_ref='op#23086,op#23087', triaged_at=now(), triaged_by='orch-console' WHERE id=202 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Student Coordinator functions checked and tested end to end', triage_evidence_ref='op#23097,op#23113', triaged_at=now(), triaged_by='orch-console' WHERE id=207 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Theory overflow explained, then fixed in new 3-week theory schedule', triage_evidence_ref='op#23098,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=208 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Periods question answered; superseded by fluid-periods instruction', triage_evidence_ref='op#23098', triaged_at=now(), triaged_by='orch-console' WHERE id=209 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='No RTA, 3-week theory, HAZMAT cascade applied on real schedule', triage_evidence_ref='op#23162,am#45272', triaged_at=now(), triaged_by='orch-console' WHERE id=217 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='SCBA now 30/15 only in regenerated files', triage_evidence_ref='op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=218 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='HAZMAT practicals 2 groups at a time applied', triage_evidence_ref='op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=220 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='HAZMAT starts with G7+G8 applied', triage_evidence_ref='op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=221 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='G5-G8 BAPT brought forward', triage_evidence_ref='op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=224 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Directive acknowledged (cai out of cosem)', triage_evidence_ref='op#23111', triaged_at=now(), triaged_by='orch-console' WHERE id=225 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Later lessons/exams used end buffer in new schedule', triage_evidence_ref='op#23113,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=226 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Preview sent, then final files delivered', triage_evidence_ref='op#23142,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=238 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='HAZMAT full fortnight fixed', triage_evidence_ref='op#23145,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=239 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='No make-up tags in new schedule', triage_evidence_ref='op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=240 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Clarification applied (labelled normally)', triage_evidence_ref='op#23148,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=241 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Circuit-breaker errors fixed, recovery confirmed', triage_evidence_ref='op#23167,op#23180', triaged_at=now(), triaged_by='orch-console' WHERE id=252 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Same errors (terminal copy); fixed and verified', triage_evidence_ref='op#23167,op#23180,am#45384', triaged_at=now(), triaged_by='orch-console' WHERE id=253 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Client Telegram channels confirmed working', triage_evidence_ref='op#23180', triaged_at=now(), triaged_by='orch-console' WHERE id=256 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Coord woken; promised work delivered', triage_evidence_ref='op#23177,op#23181', triaged_at=now(), triaged_by='orch-console' WHERE id=258 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Autoscaler status answered', triage_evidence_ref='op#23177', triaged_at=now(), triaged_by='orch-console' WHERE id=259 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='3 PDFs delivered', triage_evidence_ref='op#23214,op#23215,op#23216', triaged_at=now(), triaged_by='orch-console' WHERE id=265 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Angullia agent fixed and replied in group', triage_evidence_ref='op#23212,op#23213', triaged_at=now(), triaged_by='orch-console' WHERE id=266 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Better PDF format answered with new version', triage_evidence_ref='op#23219', triaged_at=now(), triaged_by='orch-console' WHERE id=267 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Print layout built into generator (PR#227 merged+deployed)', triage_evidence_ref='am#45548', triaged_at=now(), triaged_by='orch-console' WHERE id=268 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Fit-to-one-page PDF delivered', triage_evidence_ref='op#23223', triaged_at=now(), triaged_by='orch-console' WHERE id=270 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Syed option 1 (1 week per page scaled) delivered', triage_evidence_ref='op#23225', triaged_at=now(), triaged_by='orch-console' WHERE id=271 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa answered SCBA/day-night/HAZMAT order; v2 draft superseded by 17-wk generated schedule', triage_evidence_ref='op#22919,op#22980,op#22985,op#23105', triaged_at=now(), triaged_by='orch-console' WHERE id=8 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa generated the course docs himself on the platform', triage_evidence_ref='op#22988,op#22996', triaged_at=now(), triaged_by='orch-console' WHERE id=9 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='backlog#68 CI-reds plan delivered + PR1/PR2 checkpoints armed', triage_evidence_ref='am#44521,am#44529', triaged_at=now(), triaged_by='orch-console' WHERE id=94 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa did his first real Generate on prod ADCDA', triage_evidence_ref='op#22988,op#22996', triaged_at=now(), triaged_by='orch-console' WHERE id=96 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='PR201 deployed + smoke verified', triage_evidence_ref='am#44466', triaged_at=now(), triaged_by='orch-console' WHERE id=126 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='PR203 deployed, live smoke 6/6 PASS, Hariz told', triage_evidence_ref='am#44483,op#22875', triaged_at=now(), triaged_by='orch-console' WHERE id=127 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='orch PR #201 hub wake-floor enforcement merged 09-28 07:14Z', triage_evidence_ref='am#44545', triaged_at=now(), triaged_by='orch-console' WHERE id=128 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Column A tokens fixed + colour coding (PR#206 merged), file sent', triage_evidence_ref='am#44573,op#22929', triaged_at=now(), triaged_by='orch-console' WHERE id=130 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa confirmed 2026 October is the live period', triage_evidence_ref='op#22936', triaged_at=now(), triaged_by='orch-console' WHERE id=136 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa: yes proceed with BAPT rounds', triage_evidence_ref='op#22941', triaged_at=now(), triaged_by='orch-console' WHERE id=138 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa: all of Rescue moved to night', triage_evidence_ref='op#22947', triaged_at=now(), triaged_by='orch-console' WHERE id=140 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Prod replica smoke done; Musa told he can regenerate himself', triage_evidence_ref='am#44783,op#22987,op#23077', triaged_at=now(), triaged_by='orch-console' WHERE id=143 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Slice 3 deployed + prod smoke done', triage_evidence_ref='am#44657,am#44673', triaged_at=now(), triaged_by='orch-console' WHERE id=144 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Lane context resolved (auto-compact, later clean recycle); directive moot', triage_evidence_ref='am#44793,am#45163', triaged_at=now(), triaged_by='orch-console' WHERE id=145 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='iOS date field fix shipped to preview + replied on angullia channel', triage_evidence_ref='am#44675,op#22964', triaged_at=now(), triaged_by='orch-console' WHERE id=146 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='PR#211 retargeted/rebased onto main (merged)', triage_evidence_ref='am#44698', triaged_at=now(), triaged_by='orch-console' WHERE id=148 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Sweep answered: 13 wks does not fit even with day+night exams', triage_evidence_ref='am#44727,op#22976', triaged_at=now(), triaged_by='orch-console' WHERE id=151 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='cosem-port moved to Syed, resume verified', triage_evidence_ref='am#44884', triaged_at=now(), triaged_by='orch-console' WHERE id=153 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Min-weeks sweep answered (17); build+replica done; superseded by op#22985', triage_evidence_ref='am#44763,am#44783', triaged_at=now(), triaged_by='orch-console' WHERE id=155 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='e2e replica passed; Musa told to press', triage_evidence_ref='am#44783,op#22987', triaged_at=now(), triaged_by='orch-console' WHERE id=158 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa: ADCDA needs all 3 files today', triage_evidence_ref='op#23003', triaged_at=now(), triaged_by='orch-console' WHERE id=166 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Preview-first/promote-on-OK guardrail built', triage_evidence_ref='am#44825,am#44832', triaged_at=now(), triaged_by='orch-console' WHERE id=168 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='One-off ESB file delivered same day', triage_evidence_ref='am#44847,op#23014', triaged_at=now(), triaged_by='orch-console' WHERE id=171 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Drain postponed, acked', triage_evidence_ref='am#44833', triaged_at=now(), triaged_by='orch-console' WHERE id=172 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='duplicate', triage_summary='Duplicate of 175: fleet-wide realtime doorbell not live, Musa not told', triage_evidence_ref=NULL, triaged_at=now(), triaged_by='orch-console' WHERE id=176 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Retest split + gated real-org fix applied, revised files sent', triage_evidence_ref='am#45047,op#23074', triaged_at=now(), triaged_by='orch-console' WHERE id=183 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Held until re-token; re-token done', triage_evidence_ref='am#44876,am#44884', triaged_at=now(), triaged_by='orch-console' WHERE id=185 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Gated apply executed (after rollback+replan) and verified', triage_evidence_ref='am#45047', triaged_at=now(), triaged_by='orch-console' WHERE id=186 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Egress found NOT blocked; no allowlist needed', triage_evidence_ref='am#44958', triaged_at=now(), triaged_by='orch-console' WHERE id=187 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Tenant-scoped reads design delivered', triage_evidence_ref='am#44976,am#44990', triaged_at=now(), triaged_by='orch-console' WHERE id=188 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Residency triage done: orphaned dev-era copies, 0 overlap', triage_evidence_ref='am#44989', triaged_at=now(), triaged_by='orch-console' WHERE id=190 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Leaked pw confirmed live; rotation executed + verified', triage_evidence_ref='am#45001,am#45381', triaged_at=now(), triaged_by='orch-console' WHERE id=191 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Measured: 0 weeks saveable; answered to Musa', triage_evidence_ref='am#45108,op#23087', triaged_at=now(), triaged_by='orch-console' WHERE id=197 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='cosem-adcda + cosem-exams live on Syed after relaunch', triage_evidence_ref='am#45059,am#45354', triaged_at=now(), triaged_by='orch-console' WHERE id=199 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='4 Overview defects fixed; PR#221 merged+deployed', triage_evidence_ref='am#45116,op#23082,op#23090', triaged_at=now(), triaged_by='orch-console' WHERE id=201 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Bold/font mix fixed; spare-days sweep answered', triage_evidence_ref='am#45107,am#45108,op#23086', triaged_at=now(), triaged_by='orch-console' WHERE id=203 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='List+projection delivered; hub+workers moved to Syed; only console/cai/SRE on Musa', triage_evidence_ref='am#45119,am#45367,am#45467', triaged_at=now(), triaged_by='orch-console' WHERE id=205 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Hard rule already committed (b5f402e)', triage_evidence_ref='am#45158', triaged_at=now(), triaged_by='orch-console' WHERE id=210 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Hard rule committed, PR merged', triage_evidence_ref='am#45156,am#45167', triaged_at=now(), triaged_by='orch-console' WHERE id=211 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Misdirected; withdrawn by console', triage_evidence_ref='am#45149,am#45154', triaged_at=now(), triaged_by='orch-console' WHERE id=212 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='duplicate', triage_summary='Duplicate of 206: drag-and-drop mock-up not started', triage_evidence_ref=NULL, triaged_at=now(), triaged_by='orch-console' WHERE id=214 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Measured answer given; superseded by op#23099', triage_evidence_ref='am#45150,op#23098', triaged_at=now(), triaged_by='orch-console' WHERE id=215 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='duplicate', triage_summary='Duplicate of 213: SC partials not all closed; Musa not updated on completion', triage_evidence_ref=NULL, triaged_at=now(), triaged_by='orch-console' WHERE id=216 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='op#23099 Phase A applied on real org, docs sent', triage_evidence_ref='am#45203,am#45252,op#23162', triaged_at=now(), triaged_by='orch-console' WHERE id=219 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='cai answered advisory; Musa: cai off cosem, proceed', triage_evidence_ref='am#45182,op#23109', triaged_at=now(), triaged_by='orch-console' WHERE id=222 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Musa answered: no pause, humans keep uploading', triage_evidence_ref='op#23109', triaged_at=now(), triaged_by='orch-console' WHERE id=223 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Held; GO later given', triage_evidence_ref='am#45237', triaged_at=now(), triaged_by='orch-console' WHERE id=247 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Held; GO later given', triage_evidence_ref='am#45238', triaged_at=now(), triaged_by='orch-console' WHERE id=248 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Rotation done, final verify green', triage_evidence_ref='am#45270,am#45381', triaged_at=now(), triaged_by='orch-console' WHERE id=250 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Relaunch done, SRE boot verified green', triage_evidence_ref='am#45376,am#45381', triaged_at=now(), triaged_by='orch-console' WHERE id=251 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Relaunch proceeded; workers + hub on new pw/Syed', triage_evidence_ref='am#45354,am#45367', triaged_at=now(), triaged_by='orch-console' WHERE id=254 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='duplicate', triage_summary='Duplicate of 261: not built/live', triage_evidence_ref=NULL, triaged_at=now(), triaged_by='orch-console' WHERE id=262 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='QA-secret CI walk green, PR#831 merged, screenshots staged', triage_evidence_ref='am#45457,am#45475,am#45479', triaged_at=now(), triaged_by='orch-console' WHERE id=264 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='done', closed_at=now(), closed_reason='done', triage_summary='Week-per-page print layout PR#227 merged+deployed (then superseded by 274)', triage_evidence_ref='am#45544,am#45548', triaged_at=now(), triaged_by='orch-console' WHERE id=269 AND closed_at IS NULL;

-- ask / stays open (real, still-open asks -- triage_summary is what the digest shows)
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Self-regenerate works and the Overview/FIF outputs are live; ESB export isn''t in the app yet and the phone download fix is still pending.', triaged_at=now(), triaged_by='orch-console' WHERE id=142 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Schedule/setup UI/UX redesign is built (PR #218) and awaiting review/merge.', triaged_at=now(), triaged_by='orch-console' WHERE id=161 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Mobile multi-file download fix (per-document buttons + zip) has no shipped evidence yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=163 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-fleet-health', triage_summary='Fleet-wide real-time mid-turn doorbell for every agent hasn''t been built yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=177 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Drag-and-drop (kanban) schedule editor mock-up is paused, untouched.', triaged_at=now(), triaged_by='orch-console' WHERE id=204 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Anchor-first scheduler (fixed blocks placed first) is deferred to a deeper scheduler rebuild.', triaged_at=now(), triaged_by='orch-console' WHERE id=228 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Fluid periods (guide, not a hard rule; whole/half days) deferred to the same deeper scheduler rebuild.', triaged_at=now(), triaged_by='orch-console' WHERE id=229 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Future build: turn engine policy settings into CA-selectable options.', triaged_at=now(), triaged_by='orch-console' WHERE id=242 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Fleet console health hasn''t been explicitly re-confirmed since the last recovery.', triaged_at=now(), triaged_by='orch-console' WHERE id=257 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-fleet-health', triage_summary='Fleet-wide ''nothing forgotten on idle'' (boot reconstitution + promise tracking) is queued, not built.', triaged_at=now(), triaged_by='orch-console' WHERE id=260 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Digest-shows-only-real-requests fix: this very triage + digest change, in progress.', triaged_at=now(), triaged_by='orch-console' WHERE id=272 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Overview ''2 weeks per page'' (landscape, 9+1 pages) generator output is in progress.', triaged_at=now(), triaged_by='orch-console' WHERE id=273 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-tdu-coord', triage_summary='TDU IAM change (grant Scheduler Admin, remove secretAccessor) is still awaiting Musa; no reply yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=10 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Connect the cosem-platform-demo Vercel project to GitHub: no Musa reply yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=44 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-exams', triage_summary='Tablet onboarding PWA: OCR wiring merged (PR #217) but not deployed; blur-retake still pending.', triaged_at=now(), triaged_by='orch-console' WHERE id=103 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='New Mac Mini to Abu Dhabi + ADCDA on-prem question: no Musa answer yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=120 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Trial vs FF01 merge (A/B) decision: no answer found from Musa or Hariz.', triaged_at=now(), triaged_by='orch-console' WHERE id=124 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Downgrade the Vercel seat to Viewer (asked op#22547): no Musa confirmation yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=152 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='UX redesign before/after was delivered, but PR #218 is still open, not shipped.', triaged_at=now(), triaged_by='orch-console' WHERE id=162 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Files were sent, but the per-doc/zip iOS download fix was never actually built.', triaged_at=now(), triaged_by='orch-console' WHERE id=164 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='ESB was delivered as a one-off file; in-app ESB generation from the committed schedule still isn''t built.', triaged_at=now(), triaged_by='orch-console' WHERE id=167 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-tdu-coord', triage_summary='tdu-tools-staging Firebase region is unverified and the registry hasn''t been updated.', triaged_at=now(), triaged_by='orch-console' WHERE id=169 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-fleet-health', triage_summary='Mid-turn doorbell was acknowledged but not built (op#23138 confirms it''s still pending).', triaged_at=now(), triaged_by='orch-console' WHERE id=175 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-substrate', triage_summary='fleet_lessons table is applied, but the periodic reviewer that reads it isn''t built yet.', triaged_at=now(), triaged_by='orch-console' WHERE id=181 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Migration 081 (April-residue cleanup) is drafted and HELD; still needs Musa''s sign-off and a gated apply.', triaged_at=now(), triaged_by='orch-console' WHERE id=192 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='Drag-and-drop mock-up is still untouched and paused.', triaged_at=now(), triaged_by='orch-console' WHERE id=206 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-exams', triage_summary='3 of 4 partials are done; the last round trips are blocked, awaiting direction.', triaged_at=now(), triaged_by='orch-console' WHERE id=213 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='orch-console', triage_summary='Future build: CA-facing schedule policy knobs -- design discussion hasn''t started.', triaged_at=now(), triaged_by='orch-console' WHERE id=243 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-fleet-health', triage_summary='''Nothing forgotten on idle'' design is queued, not yet delivered.', triaged_at=now(), triaged_by='orch-console' WHERE id=261 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-orchestrator', triage_summary='CI test-debt cluster (backlog#68, hub PR1/PR2 armed) isn''t fixed yet; cc-orchestrator owns it.', triaged_at=now(), triaged_by='orch-console' WHERE id=263 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-cosem-platform', triage_summary='''2 weeks per page'' Overview layout was just delegated (bus #45551).', triaged_at=now(), triaged_by='orch-console' WHERE id=274 AND closed_at IS NULL;
UPDATE public.operator_asks SET triage_state='ask', delegated_to='cc-substrate', triage_summary='Operator-asks-ledger triage design was just delegated (bus #45552) -- this build IS the response.', triaged_at=now(), triaged_by='orch-console' WHERE id=275 AND closed_at IS NULL;

COMMIT;
