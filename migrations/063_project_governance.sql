-- 063_project_governance.sql
-- ledger: silo=tscuymavysscrvoberrr
-- assert: no_execute anon public.project_governance_audit_trigger()
-- (orchestrator substrate)
--
-- BUILD ORDER (P1, Musa op#20702/20704/20706, ratified op#20708 "agreed. proceed";
-- orch-console bus #40707). Stage A of reports/per-project-governance-design-op20702.md.
--
-- WHY: today, per-project governance (cai bound/not-bound, who authorizes a project's
-- build/scope decisions, whether that project's operators may clear its own money-path)
-- is CONVENTION ONLY -- scattered across memory files and docs/irsyad-ground-truth.md,
-- zero code enforcement. This table is the single home. Stages B/C (separate PRs) make
-- the toggles actually gate behavior; this migration only creates the registry + seeds
-- it to today's already-true-by-convention state (a pure data-honesty move, not a policy
-- change -- nothing here flips any project's actual current treatment).
--
-- THE THREE TOGGLES (see design doc for full authority model):
--   cai_enabled            -- OFF means cai precedent does NOT bind this project; Musa
--                              decides regardless of precedent (op#20704). Money/irreversible
--                              floors (require_verified_authorization.py, console app.py:422-427,
--                              irsyad_residency_purge.py, lane_watchdog.py) are UNTOUCHED by
--                              this toggle -- it never has and never will gate those.
--   operators               -- [{name, chat_id, internal}] who authorize that project's
--                              PRODUCT/SCOPING/build decisions (Stage C gate, non-money).
--   money_clearance_enabled -- DEFAULT FALSE everywhere. Stage D (separate, security-critical,
--                              cc-quality + security-reviewed PR) is the ONLY place this will
--                              ever be consulted -- this migration does not wire it to anything.
--   residency_ack           -- NULL by default. An explicit, audited operator acknowledgement
--                              for a residency/minors-PII-relevant change (op#20706: reframed as
--                              the project-operator's call for their own subjects' data, same
--                              authority shape as the other toggles -- the ONLY difference is it
--                              must be an explicit on-record decision, never a silent default).
--                              This migration does not enforce anything against it yet; it is a
--                              place to RECORD such a decision when Stage D or a future gate
--                              needs to check for one. A NULL value means "no acknowledgement on
--                              file", which must never be silently treated as consent.
--
-- APPEND-ONLY AUDIT: every INSERT/UPDATE on project_governance is captured verbatim (before/
-- after full-row snapshots) by an AFTER trigger into project_governance_audit -- no application
-- code path can change governance without leaving a durable, queryable record of exactly what
-- changed and when. The audit table has no UPDATE/DELETE grant to anyone (append-only).
--
-- RLS: authenticated (console) reads every row -- these are operational settings, not secrets;
-- the fleet console needs to render them (Stage E). Writes are service_role/postgres only --
-- this migration seeds via direct psycopg (this transaction, as postgres); Stage B/C code that
-- READS cai_enabled/operators uses the app's normal DSN (service-role-shaped); nothing outside
-- this migration and a future console-write-path (Stage E, its own reviewed PR) writes here.
-- anon gets nothing.
--
-- NO -- assert: lines: this migration contains no REVOKE/DROP FUNCTION, so none of
-- apply_migration.py's three assert kinds (no_execute/search_path/dropped) apply. Wet-proved via
-- --dry-run instead (transcript posted to orch-console per the build order -- this migration is
-- NOT applied by cc-substrate; console applies via the sanctioned direct-psycopg pattern).

CREATE TABLE IF NOT EXISTS public.project_governance (
  project                  TEXT        PRIMARY KEY,
  cai_enabled               BOOLEAN     NOT NULL,
  operators                 JSONB       NOT NULL DEFAULT '[]'::jsonb,   -- [{name, chat_id, internal: bool}]
  money_clearance_enabled   BOOLEAN     NOT NULL DEFAULT false,
  residency_ack             JSONB,                                      -- NULL = no acknowledgement on file
  updated_by                TEXT,
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.project_governance IS
  'Per-project governance registry (op#20702/20704/20706): cai ON/OFF, authorizing operators, '
  'money-clearance toggle, residency acknowledgement. Stage A of the per-project governance '
  'build (reports/per-project-governance-design-op20702.md) -- data only; Stages B/C/D wire '
  'enforcement in separate, later-gated PRs. Every change is captured in '
  'project_governance_audit by trigger, append-only.';

COMMENT ON COLUMN public.project_governance.cai_enabled IS
  'OFF => cai precedent does not bind this project (op#20704); Musa decides regardless of '
  'precedent. NEVER gates the money/irreversible floors (require_verified_authorization.py, '
  'console app.py:422-427, irsyad_residency_purge.py, lane_watchdog.py) -- those stay '
  'Musa-only unconditionally, toggle or no toggle.';

COMMENT ON COLUMN public.project_governance.operators IS
  'Array of {name, chat_id, internal: bool} -- who authorizes this project''s PRODUCT/SCOPING/'
  'build decisions (Stage C, non-money gate). chat_id is the operator''s Telegram chat id, '
  'matched against a bridge-verified inbound operator_messages row, same evidentiary shape as '
  'require_verified_authorization.py (never a console/tmux-typed claim).';

COMMENT ON COLUMN public.project_governance.money_clearance_enabled IS
  'DEFAULT FALSE. ON would let this project''s registered (external) operators clear its own '
  'money-path via their bridge-verified channel -- Stage D ONLY (separate, security-critical, '
  'cc-quality + security-reviewed PR). This migration does not wire this column to any gate; '
  'it exists so Stage D has a place to read from and so today''s all-off reality is on record.';

COMMENT ON COLUMN public.project_governance.residency_ack IS
  'NULL by default = no residency/minors-PII acknowledgement on file for this project. A '
  'non-null value must be an EXPLICIT, on-record, audited operator decision (op#20706) -- '
  'never a silent default, never inferred from the absence of an objection. Shape (fields, '
  'required evidence) is intentionally left open for whichever gate first needs to write one; '
  'this migration only reserves the column.';

-- ---------------------------------------------------------------- append-only audit trail
CREATE TABLE IF NOT EXISTS public.project_governance_audit (
  id          BIGSERIAL   PRIMARY KEY,
  project     TEXT        NOT NULL,
  before      JSONB,                          -- NULL on the row's first-ever INSERT
  after       JSONB       NOT NULL,
  changed_by  TEXT,
  reason      TEXT,
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.project_governance_audit IS
  'Append-only history of every project_governance change (written by a trigger on '
  'project_governance, never by application code directly) -- before/after full-row JSONB '
  'snapshots. No UPDATE/DELETE grant to anyone; this is the durable record of who changed a '
  'project''s governance and when.';

CREATE INDEX IF NOT EXISTS project_governance_audit_project_idx
  ON public.project_governance_audit (project, changed_at DESC);

CREATE OR REPLACE FUNCTION public.project_governance_audit_trigger()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
BEGIN
  INSERT INTO public.project_governance_audit (project, before, after, changed_by, changed_at)
  VALUES (
    NEW.project,
    CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END,
    to_jsonb(NEW),
    NEW.updated_by,
    now()
  );
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_project_governance_audit ON public.project_governance;
CREATE TRIGGER trg_project_governance_audit
  AFTER INSERT OR UPDATE ON public.project_governance
  FOR EACH ROW
  EXECUTE FUNCTION public.project_governance_audit_trigger();

-- The trigger function is SECURITY DEFINER (owned by postgres) so it can always write the
-- audit row regardless of the calling role's own grants; lock its own EXECUTE down to exactly
-- who needs to invoke it (the trigger mechanism itself, which does not go through GRANT EXECUTE
-- checks for trigger firing -- this REVOKE only prevents a role from calling it directly as an
-- ordinary function).
REVOKE ALL ON FUNCTION public.project_governance_audit_trigger() FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------- RLS
ALTER TABLE public.project_governance ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.project_governance_audit ENABLE ROW LEVEL SECURITY;

-- authenticated reads both tables (operational config + its own history, not secret); anon gets
-- nothing; writes are service_role/postgres only (no INSERT/UPDATE/DELETE policy for anyone else
-- -- RLS default-denies what no policy permits, and these tables get zero write policies).
CREATE POLICY sel_authenticated_project_governance
  ON public.project_governance FOR SELECT TO authenticated USING (true);

CREATE POLICY sel_authenticated_project_governance_audit
  ON public.project_governance_audit FOR SELECT TO authenticated USING (true);

REVOKE ALL ON public.project_governance FROM anon;
REVOKE ALL ON public.project_governance_audit FROM anon;
REVOKE INSERT, UPDATE, DELETE ON public.project_governance FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.project_governance_audit FROM authenticated;
GRANT SELECT ON public.project_governance TO authenticated;
GRANT SELECT ON public.project_governance_audit TO authenticated;

-- ---------------------------------------------------------------- seed: today's reality
-- Pure data-honesty seed -- every value here is already-true-by-convention (memory files +
-- docs/irsyad-ground-truth.md), not a new policy decision. ON CONFLICT DO NOTHING so a re-run
-- (e.g. a second --dry-run) never clobbers a value someone has since changed for real.
INSERT INTO public.project_governance (project, cai_enabled, operators, money_clearance_enabled, updated_by)
VALUES
  ('substrate', true,  '[]'::jsonb, false, 'migration-063-seed'),
  ('irsyad',    false, '[{"name":"Shuq","chat_id":"605271890","internal":false},{"name":"Wan","chat_id":"661212242","internal":false}]'::jsonb, false, 'migration-063-seed'),
  ('cosem',     false, '[{"name":"Hariz","chat_id":"-5390372474","internal":false}]'::jsonb, false, 'migration-063-seed')
ON CONFLICT (project) DO NOTHING;
