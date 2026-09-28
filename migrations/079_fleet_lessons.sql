-- 079_fleet_lessons.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Shared fleet lessons in the substrate (Musa op#23013 YES; orch-console bus #44851,
-- thread ee5797f6-e5f2-4c95-822f-4e3c8756cd2e). Moves fleet-wide "lessons" out of agents'
-- private memory files into one table every body's boot read can draw from, so a lesson one
-- lane earns the hard way doesn't die with that lane's context.
--
-- Modelled on a read-only study of Hermes' self-improvement memory (hermes-agent
-- background_review.py / tools/memory_tool.py), deliberately minus its staleness bug: Hermes'
-- memory has no verified-at concept and now contradicts itself about which host is live. Here,
-- last_verified_at + status='expired' is how a lesson ages out of the boot read instead of
-- quietly rotting in place. (The reviewer, staleness job, and boot-read wiring are separate,
-- code-only follow-ups per orch-console's ask — this migration is schema only.)
--
-- SCHEMA:
--   scope            'fleet' (every body) or 'repo:<name>' / 'client:<name>' (scoped read) --
--                     CHECK enforces the shape so a typo can't silently create an unreadable
--                     scope no boot-read query will ever match.
--   lesson/why/how_to_apply  mirrors this repo's own memory-file convention (rule, then WHY,
--                     then HOW TO APPLY) rather than inventing a new shape.
--   author_agent      plain text, NOT an FK to public.agents -- same reasoning as
--                     held_commitments.owner_agent (migration 051): the row must outlive the
--                     body that wrote it, including an ephemeral reviewer-hook session an FK
--                     to a live registry would risk rejecting.
--   normalized_hash   lesson_add.py's near-dup check is the primary gate (prose similarity,
--                     not just byte-equality); the unique index below is a DB-layer backstop
--                     that only catches an exact-text resubmission within the same active
--                     scope, not a substitute for that check.
--   status            active | superseded | expired. superseded_by links forward to the
--                     replacement so a chain of corrections stays readable, matching
--                     held_commitments' "never delete, the audit trail must survive" stance.
--
-- Per-scope character-cap enforcement (Hermes' 2200-char/scope forced-merge) is an
-- application-level SUM(char_length(lesson)) check in lesson_add.py, not a row-level
-- constraint here -- a cap is a fact about a scope's total, not about any single row.
--
-- PRIVILEGE (orch-console challenge, bus #44871): this project's pg_default_acl grants
-- anon=rxtm / authenticated=arwdxtm on every NEW table (verified live on
-- tscuymavysscrvoberrr; same empirical finding as migrations 065/068's headers). Every
-- comparable table (held_commitments, operator_asks, agent_messages, operator_messages,
-- operator_backlog) has RLS on with no anon/authenticated grants -- fleet_lessons will
-- describe hosts, paths, gates, and client handling, so the same posture applies here:
-- RLS ENABLE + a deny-all policy (service-role-only, same shape as migration 068's
-- vault-adjacent tables -- the orchestrator's own processes connect via the service DSN,
-- which bypasses RLS, so nothing here is ever console/PostgREST-readable) PLUS an explicit
-- REVOKE ALL from anon/authenticated on the table AND its bigserial sequence
-- (belt-and-braces on top of RLS, same as 068 -- RLS blocks ROW access, not the grant
-- itself). Post-apply assertions verify the REVOKEs actually stuck, not just that they ran
-- (bus #44871 also asked for a sequence-USAGE assertion; apply_migration.py's assert kinds
-- previously covered only no_table_privilege, so this migration is paired with adding
-- `no_sequence_privilege` to apply_migration.py -- see that file's diff in this same PR).
--
-- REVERT: DROP TABLE IF EXISTS public.fleet_lessons;
--
-- assert: no_table_privilege anon public.fleet_lessons SELECT
-- assert: no_table_privilege anon public.fleet_lessons INSERT
-- assert: no_table_privilege authenticated public.fleet_lessons SELECT
-- assert: no_table_privilege authenticated public.fleet_lessons INSERT
-- assert: no_table_privilege authenticated public.fleet_lessons UPDATE
-- assert: no_table_privilege authenticated public.fleet_lessons DELETE
-- assert: no_sequence_privilege anon public.fleet_lessons_id_seq USAGE
-- assert: no_sequence_privilege authenticated public.fleet_lessons_id_seq USAGE

BEGIN;

CREATE TABLE IF NOT EXISTS public.fleet_lessons (
    id                bigserial   PRIMARY KEY,

    scope             text        NOT NULL
                                   CHECK (scope = 'fleet' OR scope ~ '^(repo|client):[a-z0-9_-]+$'),

    lesson            text        NOT NULL CHECK (char_length(lesson) BETWEEN 1 AND 4000),
    why               text,
    how_to_apply      text,

    source_ref        text,       -- op#/bus#/PR this lesson came from
    author_agent      text        NOT NULL,

    created_at        timestamptz NOT NULL DEFAULT now(),
    last_verified_at  timestamptz NOT NULL DEFAULT now(),

    status            text        NOT NULL DEFAULT 'active'
                                   CHECK (status IN ('active', 'superseded', 'expired')),
    superseded_by     bigint      REFERENCES public.fleet_lessons(id),

    normalized_hash   text        NOT NULL
);

COMMENT ON TABLE public.fleet_lessons IS
  'Fleet-wide shared lessons (Musa op#23013, bus #44851). Written only via scripts/lesson_add.py, never a hand INSERT -- it owns dedup, the per-scope character cap, and the injection/secret scan. Service-role-only (RLS deny-all + explicit REVOKE) -- never console/PostgREST-readable, since lessons describe hosts, paths, gates, and client handling.';
COMMENT ON COLUMN public.fleet_lessons.scope IS
  '''fleet'' (every body''s boot read) or ''repo:<name>'' / ''client:<name>'' (scoped read).';
COMMENT ON COLUMN public.fleet_lessons.author_agent IS
  'Plain agent id, NOT an FK to public.agents -- same reasoning as held_commitments.owner_agent (051): the row must outlive the body that wrote it.';
COMMENT ON COLUMN public.fleet_lessons.last_verified_at IS
  'Ages out via the staleness reviewer: a lesson referencing hosts/leases/tokens/paths older than N days is flagged for re-verification; status=expired drops it from the boot read.';
COMMENT ON COLUMN public.fleet_lessons.normalized_hash IS
  'DB-layer backstop only -- catches an exact-text resubmission within the same active scope. lesson_add.py''s near-dup/similarity check is the primary gate.';

-- Exact-dup guard at the DB layer (defense in depth alongside lesson_add.py's near-dup check).
CREATE UNIQUE INDEX IF NOT EXISTS fleet_lessons_scope_hash_active_uidx
    ON public.fleet_lessons (scope, normalized_hash) WHERE status = 'active';

-- The boot read and the per-scope character-cap check both filter on (scope, status='active').
CREATE INDEX IF NOT EXISTS fleet_lessons_scope_status_idx ON public.fleet_lessons (scope, status);

-- The weekly staleness reviewer scans active lessons ordered by how long since verification.
CREATE INDEX IF NOT EXISTS fleet_lessons_last_verified_idx
    ON public.fleet_lessons (last_verified_at) WHERE status = 'active';

-- ---------------------------------------------------------------- RLS: deny-all, no exceptions
-- Service-role-only, same shape as migration 068's vault-adjacent tables: lesson_add.py and
-- any boot-read query connect via the service DSN (bypasses RLS); nothing here is ever
-- console/PostgREST-readable.
ALTER TABLE public.fleet_lessons ENABLE ROW LEVEL SECURITY;

CREATE POLICY deny_all_fleet_lessons
    ON public.fleet_lessons FOR ALL TO public USING (false);

-- ---------------------------------------------------------------- explicit REVOKE
-- Belt-and-braces on top of RLS: pg_default_acl grants anon/authenticated standing table
-- privileges on every new table (and USAGE on every new sequence) regardless of RLS (RLS
-- blocks ROW access, not the grant itself) -- take them back explicitly, table AND its
-- bigserial sequence, and assert (header above) that they actually came off rather than
-- trusting the REVOKE ran silently.
REVOKE ALL ON public.fleet_lessons FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.fleet_lessons_id_seq FROM anon, authenticated;

COMMIT;
